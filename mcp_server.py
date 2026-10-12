from collections.abc import Awaitable, Callable
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field
from starlette.applications import Starlette

from servicenow_client import (
    MAX_LIMIT,
    ConfigurationItem,
    ConfigurationItemList,
    get_configuration_item_from_servicenow,
    list_configuration_items_from_servicenow,
    search_configuration_items_in_servicenow,
)

AsgiMessage = dict[str, object]
AsgiScope = dict[str, object]
AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
AsgiSend = Callable[[AsgiMessage], Awaitable[None]]
ToolLimit = Annotated[int, Field(ge=1, le=MAX_LIMIT)]
ToolSysId = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{32}$")]
ToolSearchName = Annotated[str, Field(min_length=1)]


def build_mcp_server() -> MCPServer:
    server = MCPServer(
        name="servicenow_cmdb_mcp",
        description="Read-only MCP tools for ServiceNow CMDB configuration item lookup.",
        version="1.0.0",
    )

    @server.tool(
        name="list_configuration_items",
        description="Retrieve a limited read-only list of configuration items from ServiceNow cmdb_ci.",
        structured_output=True,
    )
    async def list_configuration_items(limit: ToolLimit = 10) -> ConfigurationItemList:
        return await list_configuration_items_from_servicenow(limit)

    @server.tool(
        name="get_configuration_item",
        description="Retrieve one read-only ServiceNow cmdb_ci configuration item by exact 32-character sys_id.",
        structured_output=True,
    )
    async def get_configuration_item(sys_id: ToolSysId) -> ConfigurationItem:
        return await get_configuration_item_from_servicenow(sys_id)

    @server.tool(
        name="search_configuration_items",
        description="Search read-only ServiceNow cmdb_ci configuration items by name.",
        structured_output=True,
    )
    async def search_configuration_items(name: ToolSearchName, limit: ToolLimit = 10) -> ConfigurationItemList:
        return await search_configuration_items_in_servicenow(name, limit)

    return server


def build_streamable_http_app(server: MCPServer) -> Starlette:
    return server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        host="0.0.0.0",
    )



async def mcp_app(scope: AsgiScope, receive: AsgiReceive, send: AsgiSend) -> None:
    server = build_mcp_server()
    streamable_http_app = build_streamable_http_app(server)
    async with server.session_manager.run():
        await streamable_http_app(scope, receive, send)
