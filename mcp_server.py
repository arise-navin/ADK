import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field
from starlette.applications import Starlette

from local_cmdb_mcp import request_id
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
logger = logging.getLogger("adk_mcp.inbound")
logger.setLevel(logging.INFO)
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
    started = time.monotonic()
    logger.info("[INBOUND_MCP] request_start", extra={"event": "request_start", "request_id": request_id.get()})
    server = build_mcp_server()
    streamable_http_app = build_streamable_http_app(server)
    response_status: int | None = None

    async def logged_send(message: AsgiMessage) -> None:
        nonlocal response_status
        if message["type"] == "http.response.start":
            response_status = int(message["status"])
        await send(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            logger.info("[INBOUND_MCP] response", extra={"event": "response", "request_id": request_id.get(), "status_code": response_status, "duration_ms": round((time.monotonic() - started) * 1000, 2)})

    try:
        async with server.session_manager.run():
            await streamable_http_app(scope, receive, logged_send)
    except Exception as error:
        logger.error("[INBOUND_MCP] request_failure", extra={"event": "request_failure", "request_id": request_id.get(), "error_type": type(error).__name__})
        raise
