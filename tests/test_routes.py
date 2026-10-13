import asyncio
import importlib
import json
import os

os.environ["GOOGLE_ADK_MODEL"] = "gemini-2.5-flash"
os.environ["SERVICENOW_INSTANCE_URL"] = "https://techsnitchpvtltddemo2.service-now.com"
os.environ["SERVICENOW_USERNAME"] = "test-user"
os.environ["SERVICENOW_PASSWORD"] = "test-password"

agent = importlib.import_module("agent")
servicenow_client = importlib.import_module("servicenow_client")


async def collect_response(method: str, path: str, body: bytes) -> tuple[int, bytes]:
    messages: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    await agent.app(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [[b"host", b"localhost:8001"]],
            "headers": [
                [b"host", b"localhost:8001"],
                [b"content-type", b"application/json"],
                [b"accept", b"application/json, text/event-stream"],
            ],
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


def post_mcp(payload: dict[str, object]) -> dict[str, object]:
    status, body = asyncio.run(collect_response("POST", "/mcp", json.dumps(payload).encode("utf-8")))

    assert status == 200
    return json.loads(body)


def test_root_health_route_points_to_agent_card() -> None:
    status, body = asyncio.run(collect_response("GET", "/", b""))

    assert status == 200
    assert body == b'{"status":"ok","agent_card":"/.well-known/agent-card.json"}'


def test_agent_card_preserves_adk_fields_and_adds_top_level_url(monkeypatch) -> None:
    async def get_tools(readonly_context=None) -> list[object]:
        return []

    monkeypatch.setattr(agent.cmdb_mcp_toolset, "get_tools", get_tools)
    async def check_card() -> tuple[int, bytes]:
        async with agent.a2a_app.router.lifespan_context(agent.a2a_app):
            return await collect_response("GET", "/.well-known/agent-card.json", b"")

    status, body = asyncio.run(check_card())
    card = json.loads(body)

    assert status == 200
    assert card["url"] == f"{agent.read_public_agent_url().rstrip('/')}/"
    assert card["name"] == agent.root_agent.name
    assert card["description"] == agent.root_agent.description
    assert card["skills"]
    assert card["supportedInterfaces"]


def test_mcp_initialize_returns_server_capabilities() -> None:
    response = post_mcp(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0.0"},
            },
        }
    )

    assert response["result"]["serverInfo"]["name"] == "servicenow_cmdb_mcp"
    assert "tools" in response["result"]["capabilities"]


def test_mcp_tools_list_exposes_read_only_cmdb_tools() -> None:
    response = post_mcp({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})

    tools = {tool["name"]: tool for tool in response["result"]["tools"]}

    assert set(tools) == {
        "list_configuration_items",
        "get_configuration_item",
        "search_configuration_items",
    }
    assert tools["list_configuration_items"]["inputSchema"]["properties"]["limit"]["maximum"] == 100
    assert tools["get_configuration_item"]["inputSchema"]["properties"]["sys_id"]["pattern"] == "^[0-9a-fA-F]{32}$"
    assert "sysparm_query" not in tools


def test_mcp_list_configuration_items_returns_structured_service_now_records(monkeypatch) -> None:
    async def request_cmdb_records(params: dict[str, str]) -> dict[str, object]:
        assert params == {"sysparm_limit": "3"}
        return {
            "result": [
                {
                    "sys_id": "8296b5ae0ad3370227dac480f0fc66dd",
                    "name": "MySQL FLX",
                    "sys_class_name": "cmdb_ci_database",
                    "manufacturer": "MariaDB",
                    "location": "Datacenter",
                }
            ]
        }

    monkeypatch.setattr(servicenow_client, "request_cmdb_records", request_cmdb_records)

    response = post_mcp(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_configuration_items", "arguments": {"limit": 3}},
        }
    )

    assert response["result"]["structuredContent"]["count"] == 1
    assert response["result"]["structuredContent"]["items"][0]["name"] == "MySQL FLX"


def test_mcp_rejects_invalid_sys_id_before_service_now_request(monkeypatch) -> None:
    async def request_cmdb_records(params: dict[str, str]) -> dict[str, object]:
        raise AssertionError(f"ServiceNow must not be called for invalid sys_id: {params}")

    monkeypatch.setattr(servicenow_client, "request_cmdb_records", request_cmdb_records)

    response = post_mcp(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "get_configuration_item", "arguments": {"sys_id": "invalid"}},
        }
    )

    assert response["result"]["isError"] is True
    assert "sys_id" in response["result"]["content"][0]["text"]


def test_mcp_rejects_excessive_limit_before_service_now_request(monkeypatch) -> None:
    async def request_cmdb_records(params: dict[str, str]) -> dict[str, object]:
        raise AssertionError(f"ServiceNow must not be called for invalid limit: {params}")

    monkeypatch.setattr(servicenow_client, "request_cmdb_records", request_cmdb_records)

    response = post_mcp(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "search_configuration_items", "arguments": {"name": "MySQL FLX", "limit": 101}},
        }
    )

    assert response["result"]["isError"] is True
    assert "limit" in response["result"]["content"][0]["text"]
