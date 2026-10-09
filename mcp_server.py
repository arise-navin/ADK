from mcp.server.fastmcp import FastMCP

from cmdb_tools import (
    analyze_ci_staleness as analyze_ci_staleness_impl,
    generate_stale_ci_proposal as generate_stale_ci_proposal_impl,
    get_ci_details as get_ci_details_impl,
    resolve_ci_owner as resolve_ci_owner_impl,
)

mcp = FastMCP(
    "Perimetra ServiceNow CMDB Governance",
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
def get_ci_details(sys_id: str | None = None, name: str | None = None, limit: int = 1) -> dict[str, object]:
    """Retrieve a bounded read-only ServiceNow CMDB CI result set by sys_id or name."""
    return get_ci_details_impl(sys_id=sys_id, name=name, limit=limit)


@mcp.tool()
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


@mcp.tool()
def resolve_ci_owner(sys_id: str | None = None, name: str | None = None) -> dict[str, object]:
    """Read available CMDB ownership reference fields without assuming a verified owner."""
    return resolve_ci_owner_impl(sys_id=sys_id, name=name)


@mcp.tool()
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


mcp_app = mcp.streamable_http_app()


async def handle_mcp_request(scope: dict[str, object], receive: object, send: object) -> None:
    async with mcp.session_manager.run():
        await mcp_app(scope, receive, send)
