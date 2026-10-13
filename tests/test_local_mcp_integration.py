import asyncio
import logging
import os

os.environ["GOOGLE_ADK_MODEL"] = "gemini-3.6-flash"
import agent
import servicenow_client
from local_cmdb_mcp import request_id


SYS_ID = "8296b5ae0ad3370227dac480f0fc66dd"


def test_adk_mcp_session_discovers_and_calls_local_tools(monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO, logger="adk_mcp.outbound")
    monkeypatch.setenv("GOOGLE_API_USE_CLIENT_CERTIFICATE", "false")
    requests: list[dict[str, str]] = []

    async def request_cmdb_records(params: dict[str, str]) -> dict[str, object]:
        requests.append(params)
        return {"result": [{"sys_id": SYS_ID, "name": "MySQL FLX", "sys_class_name": "cmdb_ci_db_mysql_catalog"}]}

    monkeypatch.setattr(servicenow_client, "request_cmdb_records", request_cmdb_records)

    async def run() -> None:
        toolset = agent.cmdb_mcp_toolset
        context_token = request_id.set("test-correlation")
        try:
            tools = await toolset.get_tools()
            assert {tool.name for tool in tools} == {
                "list_configuration_items",
                "search_configuration_items",
                "get_configuration_item",
            }
            session = await toolset._mcp_session_manager.create_session(headers=None)
            for name, arguments in [
                ("list_configuration_items", {"limit": 5}),
                ("search_configuration_items", {"name": "MySQL", "limit": 5}),
                ("get_configuration_item", {"sys_id": SYS_ID}),
            ]:
                result = await session.call_tool(name, arguments=arguments)
                assert result.is_error is not True
                assert result.structured_content is not None
                if name == "get_configuration_item":
                    assert result.structured_content["sys_id"] == SYS_ID
                    assert result.structured_content["sys_class_name"] == "cmdb_ci_db_mysql_catalog"
                else:
                    assert result.structured_content["items"][0]["sys_id"] == SYS_ID
                    assert result.structured_content["items"][0]["name"] == "MySQL FLX"
            invalid = await session.call_tool("get_configuration_item", arguments={"sys_id": "invalid"})
            assert invalid.is_error is True
        finally:
            await toolset.close()
            request_id.reset(context_token)

    asyncio.run(run())
    assert len(requests) == 3
    assert requests[1]["sysparm_query"] == "nameLIKEMySQL"
    assert requests[2]["sysparm_query"] == f"sys_id={SYS_ID}"
    events = [record.event for record in caplog.records if record.name == "adk_mcp.outbound"]
    assert "session_initialized" in events
    assert "tools_discovered" in events
    assert events.count("tool_call_success") == 3
    assert events.count("tool_call_failure") == 1
    assert "session_closed" in events
    assert SYS_ID not in caplog.text
    assert all(record.request_id == "test-correlation" for record in caplog.records if record.name == "adk_mcp.outbound")
