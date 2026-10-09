import os
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
from mcp_server import mcp_app

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

    if request_type == "http" and isinstance(path, str) and (
        path == "/mcp" or path.startswith("/mcp/")
    ):
        await mcp_app(scope, receive, send)
        return

    await a2a_app(scope, receive, send)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
