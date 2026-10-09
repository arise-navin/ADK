from datetime import UTC, datetime, timedelta

import pytest

import cmdb_tools
from servicenow_client import (
    ConfigurationItem,
    ServiceNowConfigurationError,
    build_cmdb_ci_params,
    read_only_operation,
)


def test_get_ci_details_rejects_unrestricted_queries() -> None:
    with pytest.raises(ValueError, match="Either sys_id or name is required"):
        cmdb_tools.get_ci_details(sys_id=None, name=None, limit=1)


def test_invalid_sys_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="32-character hexadecimal"):
        build_cmdb_ci_params(sys_id="not-valid", name=None, limit=1)


def test_limit_is_bounded() -> None:
    with pytest.raises(ValueError, match="between 1 and 10"):
        build_cmdb_ci_params(sys_id=None, name="APP-SERVER-01", limit=100)


def test_staleness_calculation_is_deterministic_recent() -> None:
    timestamp = (datetime.now(UTC) - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
    ci = ConfigurationItem(sys_id="a" * 32, name="APP-SERVER-01", sys_updated_on=timestamp)

    result = cmdb_tools.analyze_ci_staleness(
        sys_id=None,
        name=None,
        ci=ci,
        stale_days_threshold=90,
    )

    assert result["assessment"] == "current"
    assert result["risk_level"] == "low"
    assert result["record_modified"] is False


def test_missing_timestamp_returns_unknown_without_fabrication() -> None:
    ci = ConfigurationItem(sys_id="a" * 32, name="APP-SERVER-01", sys_updated_on=None)

    result = cmdb_tools.analyze_ci_staleness(
        sys_id=None,
        name=None,
        ci=ci,
        stale_days_threshold=90,
    )

    assert result["assessment"] == "unknown"
    assert result["staleness_days"] is None
    assert result["record_modified"] is False


def test_missing_credentials_fail_securely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SERVICENOW_INSTANCE_URL", raising=False)
    monkeypatch.delenv("SERVICENOW_USERNAME", raising=False)
    monkeypatch.delenv("SERVICENOW_PASSWORD", raising=False)

    with pytest.raises(ServiceNowConfigurationError, match="Missing required ServiceNow"):
        cmdb_tools.build_servicenow_client()


def test_operations_are_get_only() -> None:
    assert read_only_operation() == "GET"
