import httpx
import pytest
from pydantic import SecretStr

from servicenow_client import ServiceNowClient, ServiceNowCredentials, ServiceNowRequestError
from servicenow_client import ConfigurationItem


def build_credentials() -> ServiceNowCredentials:
    return ServiceNowCredentials(
        instance_url="https://example.service-now.com",
        username="integration.user",
        password=SecretStr("secret-password"),
    )


def test_servicenow_client_uses_get_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_get(
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "sys_id": "a" * 32,
                        "name": "APP-SERVER-01",
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    client = ServiceNowClient(credentials=build_credentials(), timeout_seconds=15.0)

    records = client.get_cmdb_ci(sys_id="a" * 32, name=None, limit=1)

    assert len(records) == 1
    assert calls[0]["url"] == "https://example.service-now.com/api/now/table/cmdb_ci"
    assert calls[0]["timeout"] == 15.0


def test_authentication_errors_do_not_leak_password(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized")

    monkeypatch.setattr(httpx, "get", fake_get)
    client = ServiceNowClient(credentials=build_credentials(), timeout_seconds=15.0)

    with pytest.raises(ServiceNowRequestError) as error_info:
        client.get_cmdb_ci(sys_id="a" * 32, name=None, limit=1)

    message = str(error_info.value)
    assert "401" in message
    assert "secret-password" not in message


def test_sys_id_object_value_is_normalized() -> None:
    ci = ConfigurationItem.model_validate(
        {
            "sys_id": {"display_value": "A" * 32, "value": "B" * 32},
            "name": {"display_value": "APP-SERVER-01", "value": "APP-SERVER-01"},
        }
    )

    assert ci.sys_id == "b" * 32


def test_malformed_sys_id_object_is_rejected() -> None:
    with pytest.raises(ValueError, match="32-character hexadecimal"):
        ConfigurationItem.model_validate(
            {
                "sys_id": {"display_value": "not-valid", "value": "not-valid"},
                "name": "APP-SERVER-01",
            }
        )
