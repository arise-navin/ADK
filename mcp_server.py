import os
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings

from cmdb_tools import (
    analyze_ci_staleness as analyze_ci_staleness_impl,
    generate_stale_ci_proposal as generate_stale_ci_proposal_impl,
    get_ci_details as get_ci_details_impl,
    resolve_ci_owner as resolve_ci_owner_impl,
)

AsgiMessage = dict[str, object]
AsgiScope = dict[str, object]
AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
AsgiSend = Callable[[AsgiMessage], Awaitable[None]]


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


def create_mcp_server() -> FastMCP:
    mcp = FastMCP(
        "Perimetra ServiceNow CMDB Governance",
        stateless_http=True,
        json_response=True,
        transport_security=build_transport_security_settings(),
    )
    mcp.tool()(get_ci_details)
    mcp.tool()(analyze_ci_staleness)
    mcp.tool()(resolve_ci_owner)
    mcp.tool()(generate_stale_ci_proposal)
    return mcp


def build_transport_security_settings() -> TransportSecuritySettings:
    return TransportSecuritySettings(
        allowed_hosts=read_allowed_hosts(),
        allowed_origins=read_allowed_origins(),
    )


def read_allowed_hosts() -> list[str]:
    configured_hosts = read_csv_env("MCP_ALLOWED_HOSTS")
    if configured_hosts:
        return configured_hosts

    hosts = ["localhost", "localhost:8001", "127.0.0.1", "127.0.0.1:8001"]
    for url_value in (
        os.getenv("AGENT_URL", ""),
        os.getenv("VERCEL_PROJECT_PRODUCTION_URL", ""),
        os.getenv("VERCEL_URL", ""),
    ):
        host = parse_host(url_value)
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def read_allowed_origins() -> list[str]:
    configured_origins = read_csv_env("MCP_ALLOWED_ORIGINS")
    if configured_origins:
        return configured_origins

    origins = ["http://localhost:8001"]
    for url_value in (
        os.getenv("AGENT_URL", ""),
        os.getenv("VERCEL_PROJECT_PRODUCTION_URL", ""),
        os.getenv("VERCEL_URL", ""),
    ):
        origin = parse_origin(url_value)
        if origin and origin not in origins:
            origins.append(origin)
    return origins


def read_csv_env(name: str) -> list[str]:
    return [value.strip() for value in os.getenv(name, "").split(",") if value.strip()]


def parse_host(url_value: str) -> str | None:
    normalized_value = url_value.strip()
    if not normalized_value:
        return None
    parsed_url = urlparse(normalized_value if "://" in normalized_value else f"https://{normalized_value}")
    return parsed_url.netloc or parsed_url.path or None


def parse_origin(url_value: str) -> str | None:
    normalized_value = url_value.strip()
    if not normalized_value:
        return None
    parsed_url = urlparse(normalized_value if "://" in normalized_value else f"https://{normalized_value}")
    if not parsed_url.netloc:
        return None
    return f"{parsed_url.scheme}://{parsed_url.netloc}"


async def handle_mcp_request(scope: AsgiScope, receive: AsgiReceive, send: AsgiSend) -> None:
    mcp = create_mcp_server()
    mcp_app = mcp.streamable_http_app()
    async with mcp.session_manager.run():
        await mcp_app(scope, receive, send)
