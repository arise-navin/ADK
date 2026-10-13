"""Connect the ADK MCP client to this application's inbound MCP route."""

import json
import logging
import time
from collections.abc import AsyncIterator
from contextvars import ContextVar

import httpx
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from starlette.types import ASGIApp


logger = logging.getLogger("adk_mcp.outbound")
logger.setLevel(logging.INFO)
request_id: ContextVar[str | None] = ContextVar("adk_mcp_request_id", default=None)


def log_mcp_event(event: str, **fields: str | int | float) -> None:
    logger.info("[ADK_MCP] %s", event, extra={"event": event, "request_id": request_id.get(), **fields})


def failure_event(method: str) -> str:
    return "tool_call_failure" if method == "tools/call" else "connection_failure"


class LoggedResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, method: str, tool_name: str, started: float) -> None:
        self._stream = stream
        self._method = method
        self._tool_name = tool_name
        self._started = started

    async def __aiter__(self) -> AsyncIterator[bytes]:
        chunks: list[bytes] = []
        async for chunk in self._stream:
            chunks.append(chunk)
            yield chunk
        body = b"".join(chunks)
        if not body:
            return
        payload = body.removeprefix(b"data: ").strip()
        try:
            result = json.loads(payload)
        except ValueError:
            log_mcp_event(failure_event(self._method), tool_name=self._tool_name, error_type="InvalidMcpResponse")
            return
        duration = round((time.monotonic() - self._started) * 1000, 2)
        if "error" in result or result.get("result", {}).get("isError"):
            log_mcp_event(failure_event(self._method), tool_name=self._tool_name, error_type="McpResponseError", duration_ms=duration)
        elif self._method == "initialize":
            log_mcp_event("connection_success", duration_ms=duration)
            log_mcp_event("session_initialized")
        elif self._method == "tools/list":
            log_mcp_event("tools_discovered", tool_count=len(result["result"]["tools"]))
        elif self._method == "tools/call":
            log_mcp_event("tool_call_success", tool_name=self._tool_name, duration_ms=duration)

    async def aclose(self) -> None:
        await self._stream.aclose()


class LocalMcpTransport(httpx.AsyncBaseTransport):
    def __init__(self, app: ASGIApp) -> None:
        self._transport = httpx.ASGITransport(app=app)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        method = ""
        tool_name = ""
        if request.method == "POST":
            payload = json.loads(await request.aread())
            method = payload.get("method", "")
            if method == "tools/call":
                tool_name = payload.get("params", {}).get("name", "")
        started = time.monotonic()
        if method == "initialize":
            log_mcp_event("connection_start")
        elif method == "tools/call":
            log_mcp_event("tool_call_start", tool_name=tool_name)
        try:
            response = await self._transport.handle_async_request(request)
            if response.status_code >= 400:
                log_mcp_event(
                    failure_event(method),
                    tool_name=tool_name,
                    status_code=response.status_code,
                    error_type="McpResponseError",
                    duration_ms=round((time.monotonic() - started) * 1000, 2),
                )
            elif method:
                response.stream = LoggedResponseStream(response.stream, method, tool_name, started)
            return response
        except Exception as error:
            log_mcp_event(
                failure_event(method),
                tool_name=tool_name,
                error_type=type(error).__name__,
                duration_ms=round((time.monotonic() - started) * 1000, 2),
            )
            raise

    async def aclose(self) -> None:
        await self._transport.aclose()
        log_mcp_event("session_closed")


def create_local_mcp_http_client(
    headers: dict[str, str] | None,
    timeout: httpx.Timeout | None,
    auth: httpx.Auth | None,
) -> httpx.AsyncClient:
    from agent import app

    return httpx.AsyncClient(
        transport=LocalMcpTransport(app),
        base_url="http://local-mcp",
        headers=headers,
        timeout=timeout,
        auth=auth,
    )


def build_local_cmdb_toolset() -> McpToolset:
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url="http://local-mcp/mcp",
            timeout=10,
            sse_read_timeout=30,
            terminate_on_close=False,
            httpx_client_factory=create_local_mcp_http_client,
        ),
        tool_filter=[
            "list_configuration_items",
            "get_configuration_item",
            "search_configuration_items",
        ],
        tool_list_cache_ttl_seconds=60,
    )
