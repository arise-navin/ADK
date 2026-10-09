import os
from http.cookies import SimpleCookie
from urllib.parse import parse_qs
from urllib.parse import urlparse
from collections.abc import Awaitable, Callable

from google.adk import Agent
from google.adk.a2a.utils.agent_to_a2a import to_a2a

from cmdb_tools import (
    analyze_ci_staleness as analyze_ci_staleness_impl,
    generate_stale_ci_proposal as generate_stale_ci_proposal_impl,
    get_ci_details as get_ci_details_impl,
    resolve_ci_owner as resolve_ci_owner_impl,
)
from mcp_server import handle_mcp_request
from servicenow_mcp_client import (
    ServiceNowMcpAuthenticationError,
    build_authorization_url,
    create_signed_state,
    exchange_authorization_code,
    list_servicenow_mcp_tools_async,
    call_servicenow_mcp_tool_async,
    read_servicenow_mcp_config_from_env,
    utc_now_epoch_seconds,
    validate_signed_state,
)

AsgiMessage = dict[str, object]
AsgiScope = dict[str, object]
AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
AsgiSend = Callable[[AsgiMessage], Awaitable[None]]

MODEL = os.getenv("GOOGLE_ADK_MODEL", "gemini-3.8-flash")
PORT = int(os.getenv("PORT", "8001"))


def get_ci_details(sys_id: str | None = None, name: str | None = None, limit: int = 1) -> dict[str, object]:
    """Retrieve a bounded read-only ServiceNow CMDB CI result set by sys_id or name."""
    return get_ci_details_impl(sys_id=sys_id, name=name, limit=limit)


def analyze_ci_staleness(
    sys_id: str | None = None,
    name: str | None = None,
    stale_days_threshold: int = 90,
) -> dict[str, object]:
    """Assess CI record-update freshness using retrieved ServiceNow data without modifying records."""
    return analyze_ci_staleness_impl(
        sys_id=sys_id,
        name=name,
        ci=None,
        stale_days_threshold=stale_days_threshold,
    )


def resolve_ci_owner(sys_id: str | None = None, name: str | None = None) -> dict[str, object]:
    """Read available CMDB ownership reference fields without assuming a verified owner."""
    return resolve_ci_owner_impl(sys_id=sys_id, name=name)


def generate_stale_ci_proposal(
    sys_id: str | None = None,
    name: str | None = None,
    recommendation_context: str | None = None,
) -> dict[str, object]:
    """Generate a reviewable stale-CI governance proposal without changing ServiceNow records."""
    return generate_stale_ci_proposal_impl(
        sys_id=sys_id,
        name=name,
        assessment=None,
        recommendation_context=recommendation_context,
    )


async def list_servicenow_mcp_tools() -> dict[str, object]:
    """Discover tools currently advertised by the configured ServiceNow MCP server."""
    tools = await list_servicenow_mcp_tools_async()
    return {
        "server": "ServiceNow MCP",
        "tools": tools,
        "tool_count": len(tools),
    }


async def call_servicenow_mcp_tool(tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
    """Call a discovered ServiceNow MCP tool by exact name with validated JSON arguments."""
    return await call_servicenow_mcp_tool_async(tool_name=tool_name, arguments=arguments)


def read_public_agent_url() -> str:
    configured_url = os.getenv("AGENT_URL", "").strip()
    if configured_url:
        return configured_url

    production_url = os.getenv("VERCEL_PROJECT_PRODUCTION_URL", "").strip()
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

    public_port = parsed_url.port or (443 if protocol == "https" else 80)
    return protocol, host, public_port

root_agent = Agent(
    name="servicenow_external_advisor",
    model=MODEL,
    description=(
        "Analyzes ServiceNow governance, CMDB, stale configuration item, "
        "and compliance questions and returns advisory recommendations."
    ),
    instruction="""
You are an external advisory agent called by ServiceNow through A2A.
Analyze information included in the request. When a user supplies a concrete
ServiceNow CI sys_id or name and asks for CMDB staleness, ownership, or a stale
CI proposal, use the available read-only CMDB tools and analyze only retrieved
ServiceNow data. Do not claim that you queried ServiceNow or changed records
unless verified tool output is provided.
Return:
1. assessment
2. key_reasons
3. recommended_action
4. risk_level (low, medium, high, or unknown)
5. requires_human_approval (true unless the request is purely informational)
Keep recommendations concise. Never perform destructive actions.
""".strip(),
    tools=[
        get_ci_details,
        analyze_ci_staleness,
        resolve_ci_owner,
        generate_stale_ci_proposal,
        list_servicenow_mcp_tools,
        call_servicenow_mcp_tool,
    ],
)

protocol, host, public_port = parse_public_agent_url(read_public_agent_url())

a2a_app = to_a2a(
    root_agent,
    host=host,
    port=public_port,
    protocol=protocol,
)


async def app(scope: AsgiScope, receive: AsgiReceive, send: AsgiSend) -> None:
    request_type = scope.get("type")
    method = scope.get("method")
    path = scope.get("path")

    if request_type == "http" and method == "GET" and path == "/":
        body = (
            b'{"status":"ok","agent_card":"/.well-known/agent-card.json"}'
        )
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
        await send({"type": "http.response.body", "body": body})
        return

    if request_type == "http" and method == "GET" and path == "/oauth/servicenow/start":
        await handle_servicenow_oauth_start(send)
        return

    if request_type == "http" and method == "GET" and path == "/oauth/servicenow/callback":
        await handle_servicenow_oauth_callback(scope, send)
        return

    if request_type == "http" and isinstance(path, str) and (
        path == "/mcp" or path.startswith("/mcp/")
    ):
        await handle_mcp_request(scope, receive, send)
        return

    await a2a_app(scope, receive, send)

async def handle_servicenow_oauth_start(send: AsgiSend) -> None:
    config = read_servicenow_mcp_config_from_env()
    state = create_signed_state(config=config, now_epoch_seconds=utc_now_epoch_seconds())
    authorization_url = build_authorization_url(config=config, state=state)
    await send(
        {
            "type": "http.response.start",
            "status": 302,
            "headers": [
                [b"location", authorization_url.encode("utf-8")],
                [
                    b"set-cookie",
                    (
                        "sn_mcp_oauth_state="
                        + state
                        + "; HttpOnly; Secure; SameSite=Lax; Path=/oauth/servicenow; Max-Age=600"
                    ).encode("utf-8"),
                ],
            ],
        }
    )
    await send({"type": "http.response.body", "body": b""})


async def handle_servicenow_oauth_callback(scope: AsgiScope, send: AsgiSend) -> None:
    query_params = parse_qs(bytes(scope.get("query_string", b"")).decode("utf-8"))
    error = first_query_value(query_params, "error")
    if error:
        await send_json(
            send,
            400,
            {
                "error": "servicenow_oauth_error",
                "message": first_query_value(query_params, "error_description") or error,
            },
        )
        return

    state = first_query_value(query_params, "state")
    code = first_query_value(query_params, "code")
    cookie_state = read_cookie(scope, "sn_mcp_oauth_state")
    if state is None or cookie_state is None or state != cookie_state:
        await send_json(
            send,
            400,
            {
                "error": "invalid_oauth_state",
                "message": "OAuth state is missing or does not match the authorization cookie.",
            },
        )
        return

    config = read_servicenow_mcp_config_from_env()
    try:
        validate_signed_state(config=config, state=state, now_epoch_seconds=utc_now_epoch_seconds())
        token = exchange_authorization_code(config=config, code=code or "")
    except ServiceNowMcpAuthenticationError as exc:
        await send_json(
            send,
            400,
            {
                "error": "servicenow_oauth_failed",
                "message": str(exc),
            },
        )
        return

    await send_json(
        send,
        200,
        {
            "status": "authorized",
            "message": (
                "OAuth exchange succeeded. This serverless deployment has no configured "
                "persistent token store, so store issued tokens securely in Vercel environment "
                "variables or add a persistent secret store before using outbound MCP tools."
            ),
            "token_type": token.token_type,
            "expires_at": token.expires_at,
            "refresh_token_issued": token.refresh_token is not None,
            "tokens_returned": False,
        },
        clear_oauth_cookie=True,
    )


def first_query_value(query_params: dict[str, list[str]], name: str) -> str | None:
    values = query_params.get(name)
    if not values:
        return None
    return values[0]


def read_cookie(scope: AsgiScope, name: str) -> str | None:
    headers = scope.get("headers", [])
    cookie = SimpleCookie()
    if isinstance(headers, list):
        for header_name, header_value in headers:
            if header_name.lower() == b"cookie":
                cookie.load(header_value.decode("utf-8"))
    morsel = cookie.get(name)
    return morsel.value if morsel is not None else None


async def send_json(
    send: AsgiSend,
    status: int,
    payload: dict[str, object],
    clear_oauth_cookie: bool = False,
) -> None:
    import json

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = [
        [b"content-type", b"application/json"],
        [b"content-length", str(len(body)).encode("ascii")],
    ]
    if clear_oauth_cookie:
        headers.append(
            [
                b"set-cookie",
                b"sn_mcp_oauth_state=; HttpOnly; Secure; SameSite=Lax; Path=/oauth/servicenow; Max-Age=0",
            ]
        )
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
