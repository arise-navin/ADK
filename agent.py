import os
from urllib.parse import urlparse

from google.adk import Agent
from google.adk.a2a.utils.agent_to_a2a import to_a2a

MODEL = os.getenv("GOOGLE_ADK_MODEL", "gemini-2.5-flash")
PORT = int(os.getenv("PORT", "8001"))
AGENT_URL = os.getenv("AGENT_URL", "").strip()

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

if AGENT_URL:
    parsed_url = urlparse(AGENT_URL)
    protocol = parsed_url.scheme or "https"
    host = parsed_url.hostname
    public_port = parsed_url.port or (443 if protocol == "https" else 80)
else:
    protocol = "http"
    host = "localhost"
    public_port = PORT

a2a_app = to_a2a(
    root_agent,
    host=host,
    port=public_port,
    protocol=protocol,
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(a2a_app, host="0.0.0.0", port=PORT)
