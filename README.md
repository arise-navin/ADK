# ServiceNow A2A External Agent

This project is a minimal Google ADK Agent2Agent endpoint for ServiceNow External AI Agent discovery and invocation.

It follows the ServiceNow A2A flow:

- ServiceNow discovers the agent card at `/.well-known/agent-card.json`.
- ServiceNow reads the card's public `url`.
- ServiceNow invokes the agent with A2A JSON-RPC `message/send`.

The app also serves a stateless inbound Streamable HTTP MCP endpoint at `/mcp` with three read-only CMDB lookup tools. The A2A agent uses those tools through ADK. An optional second ADK MCP toolset connects to the Perimetra ServiceNow server.

## Endpoints

- Health: `https://adk-eta.vercel.app/`
- Agent Card: `https://adk-eta.vercel.app/.well-known/agent-card.json`
- A2A runtime: the `url` advertised by the Agent Card
- Inbound MCP: `https://adk-eta.vercel.app/mcp`

## Perimetra outbound MCP

Set `SERVICENOW_INSTANCE_URL` to the hostname shown in the actual ServiceNow instance and `SERVICENOW_MCP_SERVER_URL` to its Perimetra server URL. The code requires HTTPS, the same host, and the exact Perimetra MCP path. Obtain a short-lived authorized token through the ServiceNow MCP Server Console authorization-code flow and provide it as `SERVICENOW_MCP_ACCESS_TOKEN`. Discover tool names through an authorized MCP `tools/list` request, verify both are read-only, then set `SERVICENOW_MCP_READ_ONLY_TOOLS` to their comma-separated protocol names. ADK discovers their schemas at runtime and exposes only these two names with a `perimetra` prefix.

This repository has no durable credential store or authorization-code callback. A token set as an environment variable will expire and requires manual replacement. Do not treat this as an unattended production OAuth integration. Add an approved durable encrypted token store and authorization-code flow before production use. No OAuth client secret or refresh token should be added to source or debug endpoints.

In ServiceNow MCP Server Console, open the Perimetra server and its Client Authorizations configuration. Create or associate an authorization-code client there, complete the console's OAuth setup, grant a user with permission to the two read-only tools, and record the server URL and actual protocol tool names. The existing client-credentials inbound integration does not establish an MCP client authorization. Confirm the instance hostname in the ServiceNow UI before setting either URL.

## Environment Variables

Required:

```text
GOOGLE_API_KEY
GOOGLE_ADK_MODEL=gemini-3.8-flash
AGENT_URL=https://adk-eta.vercel.app
```

`AGENT_URL` must be the public Vercel URL that ServiceNow can call. ServiceNow uses the Agent Card `url` for runtime invocation, so it must not point to `localhost`.

## Run Locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:GOOGLE_API_KEY="YOUR_GOOGLE_API_KEY"
$env:GOOGLE_ADK_MODEL="gemini-3.8-flash"
$env:AGENT_URL="http://localhost:8001"
uvicorn agent:app --host 0.0.0.0 --port 8001
```

Check the Agent Card:

```powershell
curl http://localhost:8001/.well-known/agent-card.json
```

## Deploy To Vercel

1. Import this repository in Vercel.
2. Add `GOOGLE_API_KEY`, `GOOGLE_ADK_MODEL`, and `AGENT_URL` in Vercel environment variables.
3. Set `GOOGLE_ADK_MODEL` to `gemini-3.8-flash`.
4. Set `AGENT_URL` to `https://adk-eta.vercel.app`, or your production Vercel/custom domain.
5. Deploy.
6. Verify `https://adk-eta.vercel.app/.well-known/agent-card.json`.
7. Confirm the Agent Card `url` uses the same public HTTPS host.

After changing `GOOGLE_ADK_MODEL` in Vercel, redeploy the project so the serverless function loads the new model value.

## Configure ServiceNow

In AI Agent Studio, create an External AI Agent with Agent2Agent protocol.

- Discovery type: Well-Known URI / Direct Configuration
- Agent card URL: `https://adk-eta.vercel.app/.well-known/agent-card.json`
- Communication mode: Synchronous
- Runtime credential alias: API Key, with a placeholder value if this proof of concept does not validate inbound credentials

Use ServiceNow's built-in discovery and activation flow. The app does not need ServiceNow instance credentials because ServiceNow is the A2A client.

## Test

```powershell
pytest
```

## Important

- Never commit API keys or `.env`.
- Rotate any credentials that were pasted into chat or committed by accident.
- Add inbound authentication, rate limits, logging, and monitoring before production use.
