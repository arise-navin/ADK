"""Connect ADK to a configured, read-only ServiceNow MCP server."""

import os
from urllib.parse import urlparse

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams


def build_servicenow_mcp_toolset() -> McpToolset | None:
    url = os.getenv("SERVICENOW_MCP_SERVER_URL", "").strip()
    if not url:
        return None

    instance_url = os.getenv("SERVICENOW_INSTANCE_URL", "").strip()
    parsed_url = urlparse(url)
    parsed_instance = urlparse(instance_url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != parsed_instance.hostname
        or parsed_url.path != "/sncapps/mcp-server/mcp/perimetra_cmdb_governance_server"
        or parsed_url.query
        or parsed_url.fragment
        or parsed_url.username
        or parsed_url.password
    ):
        raise ValueError("SERVICENOW_MCP_SERVER_URL must be the Perimetra MCP endpoint on SERVICENOW_INSTANCE_URL.")

    token = os.getenv("SERVICENOW_MCP_ACCESS_TOKEN", "").strip()
    if not token:
        raise ValueError("SERVICENOW_MCP_ACCESS_TOKEN is required when SERVICENOW_MCP_SERVER_URL is configured.")

    allowed_names = os.getenv("SERVICENOW_MCP_READ_ONLY_TOOLS", "").strip()
    tool_names = [name.strip() for name in allowed_names.split(",") if name.strip()]
    if len(tool_names) != 2 or len(set(tool_names)) != 2:
        raise ValueError("SERVICENOW_MCP_READ_ONLY_TOOLS must list the two verified read-only reliability tool names.")

    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
            sse_read_timeout=30,
        ),
        tool_filter=tool_names,
        tool_name_prefix="perimetra",
    )
