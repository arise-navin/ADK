# ServiceNow External Google ADK A2A Agent

An external Google ADK agent using Gemini and A2A, with a separate read-only MCP endpoint for Perimetra ServiceNow CMDB governance.

## Architecture

- A2A remains the public ServiceNow-facing agent protocol.
- MCP is a separate Streamable HTTP endpoint mounted at `/mcp`.
- ServiceNow CMDB access is read-only through the Table API endpoint `/api/now/table/cmdb_ci`.
- The CMDB tools only issue authenticated HTTPS `GET` requests and never modify ServiceNow records.
- A2A and MCP share the same read-only CMDB tool implementation, but the A2A endpoint is not treated as an MCP server.

Existing endpoints:

- Root health: `https://adk-eta.vercel.app/`
- Agent Card: `https://adk-eta.vercel.app/.well-known/agent-card.json`
- A2A JSON-RPC: the URL advertised by the Agent Card
- MCP Streamable HTTP: `https://adk-eta.vercel.app/mcp`

## MCP Tools

- `get_ci_details`: bounded CI lookup by `sys_id` or name.
- `analyze_ci_staleness`: calculates record-update freshness from `sys_updated_on`.
- `resolve_ci_owner`: returns available ownership references without declaring a verified owner.
- `generate_stale_ci_proposal`: returns a reviewable proposal with `record_modified: false`.

The staleness tool clearly treats `sys_updated_on` as record update freshness, not a discovery or last-seen timestamp.

## Environment Variables

Required:

```text
GOOGLE_API_KEY
SERVICENOW_INSTANCE_URL
SERVICENOW_USERNAME
SERVICENOW_PASSWORD
```

Recommended:

```text
GOOGLE_ADK_MODEL=gemini-2.5-flash
AGENT_URL=https://adk-eta.vercel.app
MCP_ALLOWED_HOSTS=adk-eta.vercel.app
MCP_ALLOWED_ORIGINS=https://adk-eta.vercel.app
```

Use a dedicated ServiceNow integration account with minimum required read permissions for CMDB CI data. Application GET-only restrictions are not a substitute for ServiceNow ACLs.

## Run locally (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:GOOGLE_API_KEY="YOUR_GOOGLE_API_KEY"
$env:SERVICENOW_INSTANCE_URL="https://your-instance.service-now.com"
$env:SERVICENOW_USERNAME="YOUR_READ_ONLY_USER"
$env:SERVICENOW_PASSWORD="YOUR_READ_ONLY_PASSWORD"
$env:PORT="8001"
uvicorn agent:app --host 0.0.0.0 --port 8001
```

Check the Agent Card at `http://localhost:8001/.well-known/agent-card.json`.
Use an MCP Streamable HTTP client against `http://localhost:8001/mcp`.

## Deploy to Vercel

1. Push these files to a GitHub repository.
2. Import the repository in Vercel.
3. Keep the root directory as the project root.
4. Add secret environment variables `GOOGLE_API_KEY`, `SERVICENOW_INSTANCE_URL`, `SERVICENOW_USERNAME`, and `SERVICENOW_PASSWORD`.
5. Set `GOOGLE_ADK_MODEL` to `gemini-2.5-flash`, or another model supported by your Google account and current ADK version.
6. Set `AGENT_URL` to `https://adk-eta.vercel.app`.
7. Deploy the existing Vercel project.
8. Verify `https://adk-eta.vercel.app/.well-known/agent-card.json`.
9. Verify MCP initialization and tool discovery at `https://adk-eta.vercel.app/mcp` with a Streamable HTTP MCP client.

The public URL in the Agent Card is important because ServiceNow uses the card's `url` field for runtime A2A invocation. On Vercel, this app reads `AGENT_URL` first, then falls back to Vercel's deployment URL environment variables. Set `AGENT_URL` explicitly for the production domain that ServiceNow should call.

## Vercel MCP Transport Notes

The MCP server uses the official Python MCP SDK Streamable HTTP ASGI app with `stateless_http=True` and `json_response=True`. This is the smallest practical fit for Vercel serverless request handling because it avoids relying on persistent in-process session state. Vercel's Python runtime supports ASGI apps, but production MCP clients must be tested against the deployed function because long-lived streaming behavior can be constrained by serverless platform limits.

The MCP SDK validates `Host` and `Origin` headers for DNS rebinding protection. Set `MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS` when using a production, preview, or custom domain.

## Connect to ServiceNow

In the ServiceNow release/UI described in the referenced A2A article, configure an External AI Agent using the public Agent Card URL:
`https://your-project.vercel.app/.well-known/agent-card.json`

Use synchronous communication mode. Configure the execution Connection & Credential Alias according to your ServiceNow release and deployment requirements, then discover and test the agent.

## Test

Run unit tests:

```powershell
pytest
```

Manual integration test:

1. Use a development or test ServiceNow instance.
2. Configure the three `SERVICENOW_*` variables for a read-only integration user.
3. Use a real CMDB CI `sys_id`.
4. Call MCP tool discovery against `/mcp`.
5. Invoke `get_ci_details` with the real `sys_id`.
6. Invoke `analyze_ci_staleness` with the same `sys_id`.
7. Send an existing A2A JSON-RPC `message/send` request and confirm the response still completes.

## Troubleshooting

- `401`: verify the ServiceNow username and password in Vercel project environment variables.
- `403`: verify ServiceNow ACLs and roles for read access to `cmdb_ci`.
- `404`: verify `SERVICENOW_INSTANCE_URL` and the `/mcp` URL, including no cross-origin redirect.
- Timeout: verify network access from Vercel to the ServiceNow instance and reduce query ambiguity.
- MCP initialization error: verify the client uses Streamable HTTP and points to `/mcp`.

## Important

- Never commit API keys or `.env` to Git.
- This project can query CMDB read-only and generate reviewable proposals, but it does not update records.
- Add authentication, input validation, rate limits, logging, and monitoring before production.
- Confirm current Google ADK/A2A and ServiceNow release compatibility before production use.
