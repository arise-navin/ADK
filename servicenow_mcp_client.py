import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

DEFAULT_SERVICENOW_MCP_SERVER_URL = (
    "https://techsnitchchpvtltddemo2.service-now.com/"
    "sncapps/mcp-server/mcp/perimetra_cmdb_governance_server"
)
DEFAULT_TOKEN_EXPIRES_SKEW_SECONDS = 60
STATE_TTL_SECONDS = 600


class ServiceNowMcpConfigurationError(RuntimeError):
    pass


class ServiceNowMcpAuthenticationError(RuntimeError):
    pass


class ServiceNowMcpRequestError(RuntimeError):
    pass


class OAuthToken(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_token: SecretStr
    token_type: str = "Bearer"
    refresh_token: SecretStr | None = None
    expires_at: int | None = None
    scope: str | None = None

    def is_expired(self, now_epoch_seconds: int, skew_seconds: int) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at <= now_epoch_seconds + skew_seconds


class OAuthTokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    access_token: str
    token_type: str = "Bearer"
    refresh_token: str | None = None
    expires_in: int | None = None
    scope: str | None = None


class ServiceNowMcpConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    server_url: str
    client_id: str
    client_secret: SecretStr
    redirect_uri: str
    authorization_url: str
    token_url: str
    scope: str | None = None
    oauth_state_secret: SecretStr
    access_token: SecretStr | None = None
    refresh_token: SecretStr | None = None
    token_expires_at: int | None = None
    timeout_seconds: float = Field(gt=0)
    sse_read_timeout_seconds: float = Field(gt=0)

    @field_validator("server_url", "redirect_uri", "authorization_url", "token_url")
    @classmethod
    def validate_https_url(cls, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value.startswith("https://"):
            raise ValueError("ServiceNow MCP OAuth URLs must use HTTPS.")
        return normalized_value

    @field_validator("client_id")
    @classmethod
    def validate_client_id(cls, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("SERVICENOW_OAUTH_CLIENT_ID must not be empty.")
        return normalized_value


class ServiceNowMcpTool(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    description: str | None = None
    inputSchema: dict[str, Any] | None = None


def read_servicenow_mcp_config_from_env() -> ServiceNowMcpConfig:
    instance_url = os.getenv("SERVICENOW_INSTANCE_URL", "").strip().rstrip("/")
    server_url = os.getenv("SERVICENOW_MCP_SERVER_URL", "").strip() or DEFAULT_SERVICENOW_MCP_SERVER_URL
    authorization_url = os.getenv("SERVICENOW_OAUTH_AUTHORIZATION_URL", "").strip()
    token_url = os.getenv("SERVICENOW_OAUTH_TOKEN_URL", "").strip()
    if instance_url:
        authorization_url = authorization_url or f"{instance_url}/oauth_auth.do"
        token_url = token_url or f"{instance_url}/oauth_token.do"

    missing_names = [
        name
        for name, value in (
            ("SERVICENOW_OAUTH_CLIENT_ID", os.getenv("SERVICENOW_OAUTH_CLIENT_ID", "")),
            ("SERVICENOW_OAUTH_CLIENT_SECRET", os.getenv("SERVICENOW_OAUTH_CLIENT_SECRET", "")),
            ("SERVICENOW_OAUTH_REDIRECT_URI", os.getenv("SERVICENOW_OAUTH_REDIRECT_URI", "")),
            ("SERVICENOW_OAUTH_STATE_SECRET", os.getenv("SERVICENOW_OAUTH_STATE_SECRET", "")),
            ("SERVICENOW_OAUTH_AUTHORIZATION_URL or SERVICENOW_INSTANCE_URL", authorization_url),
            ("SERVICENOW_OAUTH_TOKEN_URL or SERVICENOW_INSTANCE_URL", token_url),
        )
        if not value
    ]
    if missing_names:
        raise ServiceNowMcpConfigurationError(
            "Missing required ServiceNow MCP OAuth configuration: "
            + ", ".join(missing_names)
            + ". Configure these values in Vercel environment variables."
        )

    return ServiceNowMcpConfig(
        server_url=server_url,
        client_id=os.getenv("SERVICENOW_OAUTH_CLIENT_ID", ""),
        client_secret=SecretStr(os.getenv("SERVICENOW_OAUTH_CLIENT_SECRET", "")),
        redirect_uri=os.getenv("SERVICENOW_OAUTH_REDIRECT_URI", ""),
        authorization_url=authorization_url,
        token_url=token_url,
        scope=normalize_optional_string(os.getenv("SERVICENOW_OAUTH_SCOPE", "")),
        oauth_state_secret=SecretStr(os.getenv("SERVICENOW_OAUTH_STATE_SECRET", "")),
        access_token=secret_from_optional_env("SERVICENOW_OAUTH_ACCESS_TOKEN"),
        refresh_token=secret_from_optional_env("SERVICENOW_OAUTH_REFRESH_TOKEN"),
        token_expires_at=read_optional_int_env("SERVICENOW_OAUTH_TOKEN_EXPIRES_AT"),
        timeout_seconds=float(os.getenv("SERVICENOW_MCP_TIMEOUT_SECONDS", "30")),
        sse_read_timeout_seconds=float(os.getenv("SERVICENOW_MCP_SSE_READ_TIMEOUT_SECONDS", "60")),
    )


def build_authorization_url(config: ServiceNowMcpConfig, state: str) -> str:
    query_params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "state": state,
    }
    if config.scope:
        query_params["scope"] = config.scope
    return f"{config.authorization_url}?{urlencode(query_params)}"


def create_signed_state(config: ServiceNowMcpConfig, now_epoch_seconds: int) -> str:
    nonce = secrets.token_urlsafe(24)
    payload = {"nonce": nonce, "iat": now_epoch_seconds}
    encoded_payload = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    signature = sign_state_payload(encoded_payload, config.oauth_state_secret.get_secret_value())
    return f"{encoded_payload}.{signature}"


def validate_signed_state(config: ServiceNowMcpConfig, state: str, now_epoch_seconds: int) -> None:
    parts = state.split(".")
    if len(parts) != 2:
        raise ServiceNowMcpAuthenticationError("OAuth state is malformed.")
    encoded_payload, supplied_signature = parts
    expected_signature = sign_state_payload(encoded_payload, config.oauth_state_secret.get_secret_value())
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise ServiceNowMcpAuthenticationError("OAuth state signature is invalid.")
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded_payload.encode("ascii")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as error:
        raise ServiceNowMcpAuthenticationError("OAuth state payload is invalid.") from error
    issued_at = payload.get("iat")
    if not isinstance(issued_at, int):
        raise ServiceNowMcpAuthenticationError("OAuth state issue time is missing.")
    if issued_at + STATE_TTL_SECONDS < now_epoch_seconds:
        raise ServiceNowMcpAuthenticationError("OAuth state has expired.")


def sign_state_payload(encoded_payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def exchange_authorization_code(config: ServiceNowMcpConfig, code: str) -> OAuthToken:
    if not code.strip():
        raise ServiceNowMcpAuthenticationError("OAuth callback did not include an authorization code.")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.redirect_uri,
    }
    return request_oauth_token(config=config, data=data)


def refresh_access_token(config: ServiceNowMcpConfig, refresh_token: SecretStr) -> OAuthToken:
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token.get_secret_value(),
    }
    return request_oauth_token(config=config, data=data)


def request_oauth_token(config: ServiceNowMcpConfig, data: Mapping[str, str]) -> OAuthToken:
    request_data = dict(data)
    request_data["client_id"] = config.client_id
    request_data["client_secret"] = config.client_secret.get_secret_value()
    try:
        response = httpx.post(
            config.token_url,
            data=request_data,
            headers={"Accept": "application/json"},
            timeout=config.timeout_seconds,
        )
    except httpx.TimeoutException as error:
        raise ServiceNowMcpAuthenticationError(
            f"ServiceNow OAuth token request timed out. token_url={config.token_url}"
        ) from error
    except httpx.HTTPError as error:
        raise ServiceNowMcpAuthenticationError(
            f"ServiceNow OAuth token request failed before response. token_url={config.token_url}"
        ) from error

    if response.status_code >= 400:
        raise ServiceNowMcpAuthenticationError(
            "ServiceNow OAuth token request returned an error. "
            f"status_code={response.status_code} response_body={response.text[:1000]}"
        )
    try:
        token_response = OAuthTokenResponse.model_validate(response.json())
    except (ValueError, json.JSONDecodeError) as error:
        raise ServiceNowMcpAuthenticationError("ServiceNow OAuth token response was not valid JSON.") from error

    expires_at = None
    if token_response.expires_in is not None:
        expires_at = int(time.time()) + token_response.expires_in
    return OAuthToken(
        access_token=SecretStr(token_response.access_token),
        token_type=token_response.token_type,
        refresh_token=SecretStr(token_response.refresh_token) if token_response.refresh_token else None,
        expires_at=expires_at,
        scope=token_response.scope,
    )


def read_configured_oauth_token(config: ServiceNowMcpConfig) -> OAuthToken:
    if config.access_token is None:
        raise ServiceNowMcpAuthenticationError(
            "SERVICENOW_OAUTH_ACCESS_TOKEN is not configured. Complete OAuth authorization "
            "and store the issued token in Vercel environment variables or add persistent token storage."
        )
    token = OAuthToken(
        access_token=config.access_token,
        refresh_token=config.refresh_token,
        expires_at=config.token_expires_at,
        scope=config.scope,
    )
    if token.is_expired(int(time.time()), DEFAULT_TOKEN_EXPIRES_SKEW_SECONDS):
        if token.refresh_token is None:
            raise ServiceNowMcpAuthenticationError(
                "Configured ServiceNow OAuth access token is expired and no refresh token is configured."
            )
        return refresh_access_token(config=config, refresh_token=token.refresh_token)
    return token


async def list_servicenow_mcp_tools_async() -> list[dict[str, Any]]:
    config = read_servicenow_mcp_config_from_env()
    token = read_configured_oauth_token(config)
    async with streamablehttp_client(
        config.server_url,
        headers=build_mcp_headers(token),
        timeout=config.timeout_seconds,
        sse_read_timeout=config.sse_read_timeout_seconds,
    ) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()
            return [serialize_mcp_tool(tool) for tool in result.tools]


async def call_servicenow_mcp_tool_async(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    validated_tool_name = validate_tool_name(tool_name)
    validated_arguments = validate_tool_arguments(arguments)
    config = read_servicenow_mcp_config_from_env()
    token = read_configured_oauth_token(config)
    async with streamablehttp_client(
        config.server_url,
        headers=build_mcp_headers(token),
        timeout=config.timeout_seconds,
        sse_read_timeout=config.sse_read_timeout_seconds,
    ) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            available_tools = await session.list_tools()
            available_tool_names = {tool.name for tool in available_tools.tools}
            if validated_tool_name not in available_tool_names:
                raise ServiceNowMcpRequestError(
                    "Requested ServiceNow MCP tool was not advertised by tools/list. "
                    f"tool_name={validated_tool_name} available_tools={sorted(available_tool_names)}"
                )
            result = await session.call_tool(validated_tool_name, validated_arguments)
            return serialize_call_tool_result(validated_tool_name, result)


def build_mcp_headers(token: OAuthToken) -> dict[str, str]:
    return {
        "Authorization": f"{token.token_type} {token.access_token.get_secret_value()}",
        "Accept": "application/json, text/event-stream",
    }


def validate_tool_name(tool_name: str) -> str:
    normalized_tool_name = tool_name.strip()
    if not normalized_tool_name:
        raise ValueError("tool_name must not be empty.")
    if len(normalized_tool_name) > 200:
        raise ValueError("tool_name must be 200 characters or fewer.")
    return normalized_tool_name


def validate_tool_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be a JSON object.")
    return arguments


def serialize_mcp_tool(tool: Any) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.inputSchema,
    }


def serialize_call_tool_result(tool_name: str, result: Any) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "is_error": bool(result.isError),
        "structured_content": result.structuredContent,
        "content": [serialize_content_block(content_block) for content_block in result.content],
    }


def serialize_content_block(content_block: Any) -> dict[str, Any]:
    if hasattr(content_block, "model_dump"):
        return content_block.model_dump(mode="json", by_alias=True, exclude_none=True)
    return {"value": str(content_block)}


def normalize_optional_string(value: str) -> str | None:
    normalized_value = value.strip()
    return normalized_value or None


def secret_from_optional_env(name: str) -> SecretStr | None:
    value = os.getenv(name, "").strip()
    return SecretStr(value) if value else None


def read_optional_int_env(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    return int(value)


def utc_now_epoch_seconds() -> int:
    return int(datetime.now(UTC).timestamp())
