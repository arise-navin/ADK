import os
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from google.adk import Agent
from google.adk.a2a.utils.agent_to_a2a import to_a2a

AsgiMessage = dict[str, object]
AsgiScope = dict[str, object]
AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
AsgiSend = Callable[[AsgiMessage], Awaitable[None]]


def read_required_env(name: str) -> str:
    value: str = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable {name}. Configure it in Vercel.")
    return value


MODEL: str = read_required_env("GOOGLE_ADK_MODEL")
PORT: int = int(os.getenv("PORT", "8001"))


def read_public_agent_url() -> str:
    configured_url: str = os.getenv("AGENT_URL", "").strip()
    if configured_url:
        return configured_url

    production_url: str = os.getenv("VERCEL_PROJECT_PRODUCTION_URL", "").strip()
    if production_url:
        return f"https://{production_url}"

    deployment_url: str = os.getenv("VERCEL_URL", "").strip()
    if deployment_url:
        return f"https://{deployment_url}"

    return f"http://localhost:{PORT}"


def parse_public_agent_url(agent_url: str) -> tuple[str, str, int]:
    parsed_url = urlparse(agent_url)
    protocol: str = parsed_url.scheme
    host: str | None = parsed_url.hostname
    if not protocol or not host:
        raise ValueError(
            "AGENT_URL must be a complete public URL, for example "
            "'https://your-project.vercel.app'."
        )

    public_port: int = parsed_url.port or (443 if protocol == "https" else 80)
    return protocol, host, public_port


root_agent = Agent(
    name="servicenow_a2a_advisor",
    model=MODEL,
    description=(
        "Answers ServiceNow governance, CMDB, workflow, and operational questions "
        "when invoked by ServiceNow through Agent2Agent."
    ),
    instruction="""
You are an external AI agent invoked by ServiceNow through the Agent2Agent protocol.
Answer only from the user's request and any context ServiceNow includes in the A2A
message. Do not claim that you queried, updated, or verified ServiceNow records
unless the request includes that evidence. Keep responses concise, practical, and
safe for an enterprise ServiceNow operator.
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
        await send({"type": "http.response.body", "body": body})
        return

    await a2a_app(scope, receive, send)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
