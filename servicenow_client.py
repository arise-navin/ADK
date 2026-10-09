import base64
import os
import re
from collections.abc import Mapping
from typing import Literal
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

CMDB_CI_FIELDS = (
    "sys_id",
    "name",
    "sys_class_name",
    "sys_updated_on",
    "sys_created_on",
    "install_status",
    "operational_status",
    "assigned_to",
    "managed_by",
    "support_group",
    "location",
)

SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
MAX_CI_LIMIT = 10


class ServiceNowConfigurationError(RuntimeError):
    pass


class ServiceNowRequestError(RuntimeError):
    pass


class ServiceNowCredentials(BaseModel):
    model_config = ConfigDict(frozen=True)

    instance_url: str
    username: str
    password: SecretStr

    @field_validator("instance_url")
    @classmethod
    def validate_instance_url(cls, instance_url: str) -> str:
        normalized_url = instance_url.strip().rstrip("/")
        if not normalized_url.startswith("https://"):
            raise ValueError("SERVICENOW_INSTANCE_URL must be an HTTPS URL.")
        return normalized_url

    @field_validator("username")
    @classmethod
    def validate_username(cls, username: str) -> str:
        normalized_username = username.strip()
        if not normalized_username:
            raise ValueError("SERVICENOW_USERNAME must not be empty.")
        return normalized_username


class ServiceNowReference(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    display_value: str | None = None
    link: str | None = None
    value: str | None = None


class ConfigurationItem(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    sys_id: str
    name: ServiceNowReference | str | None = None
    sys_class_name: ServiceNowReference | str | None = None
    sys_updated_on: ServiceNowReference | str | None = None
    sys_created_on: ServiceNowReference | str | None = None
    install_status: ServiceNowReference | str | None = None
    operational_status: ServiceNowReference | str | None = None
    assigned_to: ServiceNowReference | str | None = None
    managed_by: ServiceNowReference | str | None = None
    support_group: ServiceNowReference | str | None = None
    location: ServiceNowReference | str | None = None

    @field_validator("sys_id", mode="before")
    @classmethod
    def normalize_sys_id(cls, sys_id: object) -> str:
        if isinstance(sys_id, str):
            return validate_sys_id(sys_id)
        if isinstance(sys_id, dict):
            value = sys_id.get("value") or sys_id.get("display_value")
            if isinstance(value, str):
                return validate_sys_id(value)
        raise ValueError("ServiceNow CMDB response sys_id must be a valid 32-character hexadecimal string.")


class ServiceNowTableResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    result: list[ConfigurationItem]


def read_servicenow_credentials_from_env() -> ServiceNowCredentials:
    instance_url = os.getenv("SERVICENOW_INSTANCE_URL", "").strip()
    username = os.getenv("SERVICENOW_USERNAME", "").strip()
    password = os.getenv("SERVICENOW_PASSWORD", "")
    missing_names = [
        name
        for name, value in (
            ("SERVICENOW_INSTANCE_URL", instance_url),
            ("SERVICENOW_USERNAME", username),
            ("SERVICENOW_PASSWORD", password),
        )
        if not value
    ]
    if missing_names:
        raise ServiceNowConfigurationError(
            "Missing required ServiceNow environment variables: "
            + ", ".join(missing_names)
            + ". Configure them in Vercel project settings."
        )
    return ServiceNowCredentials(
        instance_url=instance_url,
        username=username,
        password=SecretStr(password),
    )


def validate_sys_id(sys_id: str) -> str:
    normalized_sys_id = sys_id.strip()
    if not SYS_ID_PATTERN.fullmatch(normalized_sys_id):
        raise ValueError("sys_id must be a 32-character hexadecimal ServiceNow sys_id.")
    return normalized_sys_id.lower()


def validate_ci_limit(limit: int) -> int:
    if limit < 1 or limit > MAX_CI_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_CI_LIMIT}.")
    return limit


def validate_ci_name(name: str) -> str:
    normalized_name = name.strip()
    if len(normalized_name) < 2:
        raise ValueError("name must contain at least 2 non-space characters.")
    return normalized_name


def build_ci_query(sys_id: str | None, name: str | None) -> str:
    if sys_id:
        return f"sys_id={validate_sys_id(sys_id)}"
    if name:
        return f"nameLIKE{validate_ci_name(name)}"
    raise ValueError("Either sys_id or name is required. Unrestricted CMDB queries are not allowed.")


def build_cmdb_ci_params(sys_id: str | None, name: str | None, limit: int) -> dict[str, str]:
    return {
        "sysparm_query": build_ci_query(sys_id, name),
        "sysparm_limit": str(validate_ci_limit(limit)),
        "sysparm_fields": ",".join(CMDB_CI_FIELDS),
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "false",
    }


class ServiceNowClient:
    def __init__(self, credentials: ServiceNowCredentials, timeout_seconds: float) -> None:
        self._credentials = credentials
        self._timeout_seconds = timeout_seconds

    def get_cmdb_ci(self, sys_id: str | None, name: str | None, limit: int) -> list[ConfigurationItem]:
        params = build_cmdb_ci_params(sys_id, name, limit)
        url = urljoin(f"{self._credentials.instance_url}/", "api/now/table/cmdb_ci")
        headers = {
            "Accept": "application/json",
            "Authorization": build_basic_auth_header(self._credentials),
        }
        try:
            response = httpx.get(
                url,
                params=params,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as error:
            raise ServiceNowRequestError(
                "ServiceNow CMDB read timed out. "
                f"method=GET table=cmdb_ci params={redact_params(params)}"
            ) from error
        except httpx.HTTPError as error:
            raise ServiceNowRequestError(
                "ServiceNow CMDB read failed before receiving a valid response. "
                f"method=GET table=cmdb_ci params={redact_params(params)}"
            ) from error

        if response.status_code >= 400:
            raise ServiceNowRequestError(
                "ServiceNow CMDB read returned an error. "
                f"method=GET table=cmdb_ci status_code={response.status_code} "
                f"params={redact_params(params)} response_body={response.text[:1000]}"
            )

        return ServiceNowTableResponse.model_validate(response.json()).result


def build_basic_auth_header(credentials: ServiceNowCredentials) -> str:
    token = f"{credentials.username}:{credentials.password.get_secret_value()}"
    encoded_token = base64.b64encode(token.encode("utf-8")).decode("ascii")
    return f"Basic {encoded_token}"


def redact_params(params: Mapping[str, str]) -> dict[str, str]:
    return dict(params)


def read_only_operation() -> Literal["GET"]:
    return "GET"
