import os
import re
from typing import Annotated

import httpx
from pydantic import BaseModel, Field, HttpUrl, SecretStr

CMDB_FIELDS: tuple[str, ...] = ("sys_id", "name", "sys_class_name", "manufacturer", "location")
MAX_LIMIT: int = 100
REQUEST_TIMEOUT_SECONDS: float = 10.0
SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


class ServiceNowConfig(BaseModel):
    instance_url: HttpUrl
    username: str = Field(min_length=1)
    password: SecretStr = Field(min_length=1)


class ConfigurationItem(BaseModel):
    sys_id: str
    name: str | None = None
    sys_class_name: str | None = None
    manufacturer: str | None = None
    location: str | None = None


class ConfigurationItemList(BaseModel):
    items: list[ConfigurationItem]
    count: int


Limit = Annotated[int, Field(ge=1, le=MAX_LIMIT)]


class ServiceNowClientError(RuntimeError):
    pass


class ServiceNowAuthenticationError(ServiceNowClientError):
    pass


class ServiceNowAuthorizationError(ServiceNowClientError):
    pass


class ServiceNowRateLimitError(ServiceNowClientError):
    pass


class ServiceNowRequestError(ServiceNowClientError):
    pass


class ConfigurationItemNotFoundError(ServiceNowClientError):
    pass


def read_servicenow_config() -> ServiceNowConfig:
    return ServiceNowConfig(
        instance_url=os.environ.get("SERVICENOW_INSTANCE_URL", "").strip(),
        username=os.environ.get("SERVICENOW_USERNAME", "").strip(),
        password=SecretStr(os.environ.get("SERVICENOW_PASSWORD", "").strip()),
    )


def validate_limit(limit: int) -> int:
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}. Received {limit}.")
    return limit


def validate_sys_id(sys_id: str) -> str:
    normalized_sys_id = sys_id.strip()
    if SYS_ID_PATTERN.fullmatch(normalized_sys_id) is None:
        raise ValueError("sys_id must be a 32-character hexadecimal ServiceNow sys_id.")
    return normalized_sys_id.lower()


def validate_search_name(name: str) -> str:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("name must be a nonempty search string.")
    return normalized_name


async def list_configuration_items_from_servicenow(limit: int) -> ConfigurationItemList:
    validated_limit = validate_limit(limit)
    response = await request_cmdb_records({"sysparm_limit": str(validated_limit)})
    return build_configuration_item_list(response)


async def get_configuration_item_from_servicenow(sys_id: str) -> ConfigurationItem:
    validated_sys_id = validate_sys_id(sys_id)
    response = await request_cmdb_records(
        {
            "sysparm_query": f"sys_id={validated_sys_id}",
            "sysparm_limit": "1",
        }
    )
    items = build_configuration_item_list(response).items
    if not items:
        raise ConfigurationItemNotFoundError(f"Configuration item {validated_sys_id} was not found in cmdb_ci.")
    return items[0]


async def search_configuration_items_in_servicenow(name: str, limit: int) -> ConfigurationItemList:
    validated_name = validate_search_name(name)
    validated_limit = validate_limit(limit)
    response = await request_cmdb_records(
        {
            "sysparm_query": f"nameLIKE{validated_name}",
            "sysparm_limit": str(validated_limit),
        }
    )
    return build_configuration_item_list(response)


async def request_cmdb_records(params: dict[str, str]) -> dict[str, object]:
    config = read_servicenow_config()
    request_params = {
        "sysparm_fields": ",".join(CMDB_FIELDS),
        **params,
    }
    url = f"{str(config.instance_url).rstrip('/')}/api/now/table/cmdb_ci"

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                url,
                params=request_params,
                auth=(config.username, config.password.get_secret_value()),
                headers={"Accept": "application/json"},
            )
    except httpx.TimeoutException as error:
        raise ServiceNowRequestError(f"ServiceNow request timed out after {REQUEST_TIMEOUT_SECONDS} seconds.") from error
    except httpx.RequestError as error:
        raise ServiceNowRequestError(f"ServiceNow request failed for GET {url}: {error}") from error

    if response.status_code == 401:
        raise ServiceNowAuthenticationError("ServiceNow authentication failed for cmdb_ci Table API request.")
    if response.status_code == 403:
        raise ServiceNowAuthorizationError("ServiceNow authorization failed for cmdb_ci Table API request.")
    if response.status_code == 429:
        raise ServiceNowRateLimitError("ServiceNow rate limit was exceeded for cmdb_ci Table API request.")
    if response.status_code >= 400:
        raise ServiceNowRequestError(
            f"ServiceNow cmdb_ci Table API request failed with HTTP {response.status_code}: {response.text}"
        )

    try:
        response_body = response.json()
    except ValueError as error:
        raise ServiceNowRequestError("ServiceNow cmdb_ci Table API response was not valid JSON.") from error

    if not isinstance(response_body, dict):
        raise ServiceNowRequestError("ServiceNow cmdb_ci Table API response must be a JSON object.")
    return response_body


def build_configuration_item_list(response_body: dict[str, object]) -> ConfigurationItemList:
    result = response_body.get("result")
    if not isinstance(result, list):
        raise ServiceNowRequestError("ServiceNow cmdb_ci Table API response must contain a result array.")

    items = [ConfigurationItem.model_validate(item) for item in result if isinstance(item, dict)]
    if len(items) != len(result):
        raise ServiceNowRequestError("ServiceNow cmdb_ci Table API result array must contain only objects.")

    return ConfigurationItemList(items=items, count=len(items))
