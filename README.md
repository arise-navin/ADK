# ServiceNow External Google ADK A2A Agent

A starter external agent using Google ADK and the A2A protocol. It provides advisory analysis only; it does not connect to or update ServiceNow records.

## Run locally (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:GOOGLE_API_KEY="YOUR_GOOGLE_API_KEY"
$env:PORT="8001"
uvicorn agent:a2a_app --host 0.0.0.0 --port 8001
```

Check the Agent Card at `http://localhost:8001/.well-known/agent-card.json`.

## Deploy to Render

1. Push these files to a GitHub repository.
2. Create a Render Web Service from that repository.
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn agent:a2a_app --host 0.0.0.0 --port $PORT`
5. Add secret environment variable `GOOGLE_API_KEY`.
6. Set `AGENT_URL` to your public Render HTTPS URL, for example `https://your-service.onrender.com`.
7. Set `GOOGLE_ADK_MODEL` to `gemini-2.5-flash`, or another model supported by your Google account and current ADK version.
8. Deploy, then verify `https://your-service.onrender.com/.well-known/agent-card.json`.

The public URL in `AGENT_URL` is important so the generated Agent Card advertises the externally reachable address, not localhost.

## Connect to ServiceNow

In the ServiceNow release/UI described in the referenced A2A article, configure an External AI Agent using the public Agent Card URL:
`https://your-service.onrender.com/.well-known/agent-card.json`

Configure authentication according to your ServiceNow release and deployment requirements, then discover and test the agent.

## Important

- Never commit API keys or `.env` to Git.
- This starter does not query CMDB, create governance proposals, or update records.
- Add authentication, input validation, rate limits, logging, and monitoring before production.
- Confirm current Google ADK/A2A and ServiceNow release compatibility before production use.
