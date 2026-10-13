"""Opt-in read-only verification against the deployed CMDB MCP endpoint."""

import asyncio
import os

import pytest
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from servicenow_client import ConfigurationItemList


def test_live_cmdb_mcp_tools() -> None:
    if os.getenv("RUN_LIVE_MCP") != "1":
        pytest.skip("Set RUN_LIVE_MCP=1 to call the deployed read-only MCP endpoint.")
    url = os.environ["LIVE_MCP_URL"]
    token = os.getenv("LIVE_MCP_ACCESS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def verify() -> None:
        toolset = McpToolset(
            connection_params=StreamableHTTPConnectionParams(url=url, headers=headers, timeout=10, sse_read_timeout=30),
            tool_filter=["list_configuration_items", "search_configuration_items", "get_configuration_item"],
        )
        try:
            discovered = {tool.name for tool in await toolset.get_tools()}
            assert discovered == {"list_configuration_items", "search_configuration_items", "get_configuration_item"}
            session = await toolset._mcp_session_manager.create_session(headers=None)
            search = await session.call_tool("search_configuration_items", arguments={"name": "MySQL", "limit": 5})
            assert search.is_error is not True
            matches = ConfigurationItemList.model_validate(search.structured_content)
            assert matches.items
            for item in matches.items:
                assert "mysql" in (item.name or "").lower()
            for name, arguments in [
                ("list_configuration_items", {"limit": 1}),
                ("get_configuration_item", {"sys_id": matches.items[0].sys_id}),
            ]:
                result = await session.call_tool(name, arguments=arguments)
                assert result.is_error is not True
                assert result.structured_content is not None
        finally:
            await toolset.close()

    asyncio.run(verify())
