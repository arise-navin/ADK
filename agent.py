import os
from urllib.parse import urlparse
from collections.abc import Awaitable, Callable

from google.adk import Agent
from google.adk.a2a.utils.agent_to_a2a import to_a2a

AsgiMessage = dict[str, object]
AsgiScope = dict[str, object]
AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
AsgiSend = Callable[[AsgiMessage], Awaitable[None]]

MODEL = os.getenv("GOOGLE_ADK_MODEL", "gemini-2.5-flash")
PORT = int(os.getenv("PORT", "8001"))


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
Analyze only information included in the request. Do not claim that you
queried ServiceNow or changed records unless verified tool output is provided.
Return:
1. assessment
2. key_reasons
3. recommended_action
4. risk_level (low, medium, high, or unknown)
5. requires_human_approval (true unless the request is purely informational)
Keep recommendations concise. Never perform destructive actions.
""".strip(),
    tools=[],
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

    await a2a_app(scope, receive, send)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
