import asyncio
import importlib
import os

os.environ["GOOGLE_ADK_MODEL"] = "gemini-2.5-flash"

agent = importlib.import_module("agent")


async def collect_response(method: str, path: str) -> tuple[int, bytes]:
    messages: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    await agent.app(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [[b"host", b"localhost:8001"]],
            "query_string": b"",
        },
        receive,
        send,
    )
    status_messages = [message for message in messages if message["type"] == "http.response.start"]
    body_messages = [message for message in messages if message["type"] == "http.response.body"]
    status = int(status_messages[0]["status"])
    body = b"".join(bytes(message.get("body", b"")) for message in body_messages)
    return status, body


def test_root_health_route_points_to_agent_card() -> None:
    status, body = asyncio.run(collect_response("GET", "/"))

    assert status == 200
    assert body == b'{"status":"ok","agent_card":"/.well-known/agent-card.json"}'
