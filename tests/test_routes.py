import asyncio

from agent import app


async def collect_response(method: str, path: str) -> tuple[int, bytes]:
    messages: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    await app(
        {"type": "http", "method": method, "path": path, "headers": [], "query_string": b""},
        receive,
        send,
    )
    status_messages = [message for message in messages if message["type"] == "http.response.start"]
    body_messages = [message for message in messages if message["type"] == "http.response.body"]
    status = int(status_messages[0]["status"])
    body = b"".join(bytes(message.get("body", b"")) for message in body_messages)
    return status, body


def test_root_health_route_remains_available() -> None:
    status, body = asyncio.run(collect_response("GET", "/"))

    assert status == 200
    assert b"agent_card" in body


def test_mcp_route_is_separate_from_root() -> None:
    status, body = asyncio.run(collect_response("GET", "/mcp"))

    assert status in {200, 405, 406}
    assert b"agent_card" not in body
