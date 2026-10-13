import pytest

from servicenow_mcp_client import build_servicenow_mcp_toolset


def test_outbound_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SERVICENOW_MCP_SERVER_URL", raising=False)
    assert build_servicenow_mcp_toolset() is None


def test_outbound_rejects_unapproved_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVICENOW_INSTANCE_URL", "https://instance.service-now.com")
    monkeypatch.setenv("SERVICENOW_MCP_SERVER_URL", "https://evil.example/sncapps/mcp-server/mcp/perimetra_cmdb_governance_server")
    with pytest.raises(ValueError, match="Perimetra MCP endpoint"):
        build_servicenow_mcp_toolset()


def test_outbound_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVICENOW_INSTANCE_URL", "https://instance.service-now.com")
    monkeypatch.setenv("SERVICENOW_MCP_SERVER_URL", "https://instance.service-now.com/sncapps/mcp-server/mcp/perimetra_cmdb_governance_server")
    monkeypatch.delenv("SERVICENOW_MCP_ACCESS_TOKEN", raising=False)
    with pytest.raises(ValueError, match="SERVICENOW_MCP_ACCESS_TOKEN"):
        build_servicenow_mcp_toolset()
