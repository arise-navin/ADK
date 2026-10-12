# ServiceNow A2A External Agent

This project is a minimal Google ADK Agent2Agent endpoint for ServiceNow External AI Agent discovery and invocation.

It follows the ServiceNow A2A flow:

- ServiceNow discovers the agent card at `/.well-known/agent-card.json`.
- ServiceNow reads the card's public `url`.
- ServiceNow invokes the agent with A2A JSON-RPC `message/send`.

There is no MCP endpoint, OAuth route, or direct ServiceNow Table API client in this app. ServiceNow remains the primary agent and calls this Vercel-hosted external A2A agent through AI Agent Fabric.

## Endpoints

- Health: `https://adk-eta.vercel.app/`
- Agent Card: `https://adk-eta.vercel.app/.well-known/agent-card.json`
- A2A runtime: the `url` advertised by the Agent Card

## Environment Variables

Required:

```text
GOOGLE_API_KEY
GOOGLE_ADK_MODEL=gemini-2.5-flash
AGENT_URL=https://adk-eta.vercel.app
```

`AGENT_URL` must be the public Vercel URL that ServiceNow can call. ServiceNow uses the Agent Card `url` for runtime invocation, so it must not point to `localhost`.

## Run Locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:GOOGLE_API_KEY="YOUR_GOOGLE_API_KEY"
$env:GOOGLE_ADK_MODEL="gemini-2.5-flash"
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
3. Set `AGENT_URL` to `https://adk-eta.vercel.app`, or your production Vercel/custom domain.
4. Deploy.
5. Verify `https://adk-eta.vercel.app/.well-known/agent-card.json`.
6. Confirm the Agent Card `url` uses the same public HTTPS host.

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
