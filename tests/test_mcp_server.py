import asyncio

from mcp_server import create_mcp_server


def test_existing_local_mcp_tools_remain_registered() -> None:
    async def list_tool_names() -> list[str]:
        tools = await create_mcp_server().list_tools()
        return sorted(tool.name for tool in tools)

    tool_names = asyncio.run(list_tool_names())

    assert "get_ci_details" in tool_names
    assert "analyze_ci_staleness" in tool_names
    assert "resolve_ci_owner" in tool_names
    assert "generate_stale_ci_proposal" in tool_names
