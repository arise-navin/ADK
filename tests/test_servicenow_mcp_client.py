import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

import servicenow_mcp_client
from servicenow_mcp_client import (
    ServiceNowMcpAuthenticationError,
    ServiceNowMcpConfig,
    build_authorization_url,
    create_signed_state,
    exchange_authorization_code,
    validate_signed_state,
)


def build_config() -> ServiceNowMcpConfig:
    return ServiceNowMcpConfig(
        server_url="https://example.service-now.com/sncapps/mcp-server/mcp/server",
        client_id="client-id",
        client_secret=SecretStr("client-secret"),
        redirect_uri="https://adk-eta.vercel.app/oauth/servicenow/callback",
        authorization_url="https://example.service-now.com/oauth_auth.do",
        token_url="https://example.service-now.com/oauth_token.do",
        scope=None,
        oauth_state_secret=SecretStr("state-secret"),
        timeout_seconds=30,
        sse_read_timeout_seconds=60,
    )


def test_authorization_url_uses_configured_redirect_uri() -> None:
    config = build_config()
    state = create_signed_state(config=config, now_epoch_seconds=int(time.time()))

    authorization_url = build_authorization_url(config=config, state=state)

    assert authorization_url.startswith("https://example.service-now.com/oauth_auth.do?")
    assert "client_id=client-id" in authorization_url
    assert "redirect_uri=https%3A%2F%2Fadk-eta.vercel.app%2Foauth%2Fservicenow%2Fcallback" in authorization_url


def test_state_validation_rejects_mismatch() -> None:
    config = build_config()
    state = create_signed_state(config=config, now_epoch_seconds=int(time.time()))

    with pytest.raises(ServiceNowMcpAuthenticationError, match="signature"):
        validate_signed_state(config=config, state=state + "x", now_epoch_seconds=int(time.time()))


def test_token_exchange_error_does_not_leak_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(
        url: str,
        data: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized")

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(ServiceNowMcpAuthenticationError) as error_info:
        exchange_authorization_code(config=build_config(), code="auth-code")

    message = str(error_info.value)
    assert "401" in message
    assert "client-secret" not in message
    assert "auth-code" not in message


def test_refresh_token_is_used_for_expired_configured_token(monkeypatch: pytest.MonkeyPatch) -> None:
    config = build_config().model_copy(
        update={
            "access_token": SecretStr("expired-token"),
            "refresh_token": SecretStr("refresh-token"),
            "token_expires_at": 1,
        }
    )

    def fake_refresh_access_token(
        config: ServiceNowMcpConfig,
        refresh_token: SecretStr,
    ) -> servicenow_mcp_client.OAuthToken:
        return servicenow_mcp_client.OAuthToken(access_token=SecretStr("fresh-token"))

    monkeypatch.setattr(servicenow_mcp_client, "refresh_access_token", fake_refresh_access_token)

    token = servicenow_mcp_client.read_configured_oauth_token(config)

    assert token.access_token.get_secret_value() == "fresh-token"


def test_tool_name_validation_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="tool_name"):
        servicenow_mcp_client.validate_tool_name("   ")


def test_tool_discovery_uses_configured_streamable_http_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    config = build_config().model_copy(update={"access_token": SecretStr("access-token")})
    monkeypatch.setattr(servicenow_mcp_client, "read_servicenow_mcp_config_from_env", lambda: config)

    @asynccontextmanager
    async def fake_streamable_client(
        url: str,
        headers: dict[str, str],
        timeout: float,
        sse_read_timeout: float,
    ):
        calls.append(
            {
                "url": url,
                "headers": headers,
                "timeout": timeout,
                "sse_read_timeout": sse_read_timeout,
            }
        )
        yield object(), object(), lambda: None

    class FakeClientSession:
        def __init__(self, read_stream: object, write_stream: object) -> None:
            self._read_stream = read_stream
            self._write_stream = write_stream

        async def __aenter__(self) -> "FakeClientSession":
            return self

        async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
            return None

        async def initialize(self) -> None:
            calls.append({"initialized": True})

        async def list_tools(self) -> SimpleNamespace:
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="CMDB CI Reliability Status",
                        description="Returns CI reliability status.",
                        inputSchema={"type": "object"},
                    )
                ]
            )

    monkeypatch.setattr(servicenow_mcp_client, "streamablehttp_client", fake_streamable_client)
    monkeypatch.setattr(servicenow_mcp_client, "ClientSession", FakeClientSession)

    tools = asyncio_run(servicenow_mcp_client.list_servicenow_mcp_tools_async)

    assert calls[0]["url"] == "https://example.service-now.com/sncapps/mcp-server/mcp/server"
    assert calls[0]["headers"]["Authorization"] == "Bearer access-token"
    assert calls[1]["initialized"] is True
    assert tools[0]["name"] == "CMDB CI Reliability Status"


def asyncio_run(function: Callable[[], Awaitable[Any]]) -> Any:
    import asyncio

    return asyncio.run(function())
