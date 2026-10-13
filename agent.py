
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from google.adk import Agent
from google.adk.a2a.utils.agent_to_a2a import to_a2a

from local_cmdb_mcp import build_local_cmdb_toolset, request_id
from mcp_server import mcp_app
from servicenow_mcp_client import build_servicenow_mcp_toolset


AsgiMessage = dict[str, object]
AsgiScope = dict[str, object]
AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
AsgiSend = Callable[[AsgiMessage], Awaitable[None]]


def read_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(
            f"Missing required environment variable {name}. "
            "Configure it in Vercel."
        )
    return value


MODEL = read_required_env("GOOGLE_ADK_MODEL")
PORT = int(os.getenv("PORT", "8001"))


def read_public_agent_url() -> str:
    configured_url = os.getenv("AGENT_URL", "").strip()
    if configured_url:
        return configured_url

    production_url = os.getenv(
        "VERCEL_PROJECT_PRODUCTION_URL", ""
    ).strip()
    if production_url:
        return f"https://{production_url}"

    deployment_url = os.getenv("VERCEL_URL", "").strip()
    if deployment_url:
        return f"https://{deployment_url}"

    return f"http://localhost:{PORT}"


def parse_public_agent_url(agent_url: str) -> tuple[str, str, int]:
    parsed_url = urlparse(agent_url)
    protocol = parsed_url.scheme
    host = parsed_url.hostname

    if not protocol or not host:
        raise ValueError(
            "AGENT_URL must be a complete public URL, for example "
            "'https://your-project.vercel.app'."
        )

    public_port = parsed_url.port or (
        443 if protocol == "https" else 80
    )
    return protocol, host, public_port


cmdb_mcp_toolset = build_local_cmdb_toolset()

servicenow_mcp_toolset = build_servicenow_mcp_toolset()
agent_tools = [cmdb_mcp_toolset]
if servicenow_mcp_toolset is not None:
    agent_tools.append(servicenow_mcp_toolset)


root_agent = Agent(
    name="servicenow_a2a_advisor",
    model=MODEL,
    description=(
        "Answers ServiceNow governance, CMDB, workflow, and operational "
        "questions through Agent2Agent and retrieves live CMDB records "
        "using read-only ServiceNow MCP tools."
    ),
    instruction="""
You are an external AI agent invoked through the Agent2Agent protocol.

You have access to three read-only ServiceNow CMDB MCP tools:
- list_configuration_items
- get_configuration_item
- search_configuration_items

When configured, you also have dynamically discovered Perimetra MCP tools
for CI reliability status and topology. Use those tools for reliability
requests. Their names and arguments come from live MCP discovery; never
guess a tool name or argument schema.

When a user requests live CMDB information, you MUST call the appropriate
MCP tool. Do not claim that MCP tools are unavailable without attempting
the relevant tool call.

For a request to retrieve the first three configuration items, call
list_configuration_items with limit=3.

For a request to retrieve a specific configuration item by sys_id, call
get_configuration_item with the supplied sys_id.

For a configuration item name search, call search_configuration_items
with the requested name and an appropriate limit.

Return actual tool results. Include name, sys_id, and sys_class_name
when those fields are available.

Never fabricate records, identifiers, query results, or claim that a
query succeeded if the tool call failed. If a tool call fails, clearly
report the failure without generating sample data.

Use only available read-only CMDB and reliability tools. Never attempt to create,
update, delete, or deactivate ServiceNow records.

For other ServiceNow questions, answer concisely and practically.
Do not claim that you queried or verified records unless actual tool
results support that statement.
""".strip(),
    tools=agent_tools,
)


protocol, host, public_port = parse_public_agent_url(
    read_public_agent_url()
)

a2a_app = to_a2a(
    root_agent,
    host=host,
    port=public_port,
    protocol=protocol,
)

a2a_logger = logging.getLogger("adk_mcp.a2a")
a2a_logger.setLevel(logging.INFO)


async def send_compatible_agent_card(
    scope: AsgiScope,
    receive: AsgiReceive,
    send: AsgiSend,
) -> None:
    response_start: AsgiMessage | None = None
    body = bytearray()

    async def capture(message: AsgiMessage) -> None:
        nonlocal response_start
        if message["type"] == "http.response.start":
            response_start = message
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    await a2a_app(scope, receive, capture)
    if response_start is None:
        raise RuntimeError("A2A Agent Card response did not include HTTP headers.")
    if response_start["status"] != 200:
        await send(response_start)
        await send({"type": "http.response.body", "body": bytes(body)})
        return

    card = json.loads(body)
    interfaces = card.get("supportedInterfaces")
    if not isinstance(interfaces, list) or not interfaces or not isinstance(interfaces[0], dict):
        raise ValueError("A2A Agent Card is missing its supported interface.")
    interface_url = interfaces[0].get("url")
    if not isinstance(interface_url, str) or not interface_url:
        raise ValueError("A2A Agent Card supported interface is missing its URL.")
    card["url"] = f"{read_public_agent_url().rstrip('/')}/"
    updated_body = json.dumps(card, separators=(",", ":")).encode("utf-8")
    headers = [header for header in response_start["headers"] if header[0].lower() != b"content-length"]
    headers.append([b"content-length", str(len(updated_body)).encode("ascii")])
    await send({**response_start, "headers": headers})
    await send({"type": "http.response.body", "body": updated_body})


async def app(
    scope: AsgiScope,
    receive: AsgiReceive,
    send: AsgiSend,
) -> None:
    request_type = scope.get("type")
    method = scope.get("method")
    path = scope.get("path")

    if request_type == "http" and method == "GET" and path == "/":
        body = b'{"status":"ok","agent_card":"/.well-known/agent-card.json"}'
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(body)).encode("ascii")],
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
            }
        )
        return

    if (
        request_type == "http"
        and isinstance(path, str)
        and (path == "/mcp" or path.startswith("/mcp/"))
    ):
        await mcp_app(scope, receive, send)
        return

    if request_type == "http" and method == "GET" and path == "/.well-known/agent-card.json":
        await send_compatible_agent_card(scope, receive, send)
        return

    if request_type != "http" or method != "POST" or path != "/":
        await a2a_app(scope, receive, send)
        return

    correlation_id = uuid.uuid4().hex
    token = request_id.set(correlation_id)
    a2a_logger.info("[ADK_MCP] a2a_request_received", extra={"event": "a2a_request_received", "request_id": correlation_id})
    response_status: int | None = None

    async def logged_send(message: AsgiMessage) -> None:
        nonlocal response_status
        if message["type"] == "http.response.start":
            response_status = int(message["status"])
        await send(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            a2a_logger.info("[ADK_MCP] a2a_response", extra={"event": "a2a_response", "request_id": correlation_id, "status_code": response_status})

    try:
        await a2a_app(scope, receive, logged_send)
    finally:
        request_id.reset(token)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
