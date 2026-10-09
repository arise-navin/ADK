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

## Deploy to Vercel

1. Push these files to a GitHub repository.
2. Import the repository in Vercel.
3. Keep the root directory as the project root.
4. Add secret environment variable `GOOGLE_API_KEY`.
5. Set `GOOGLE_ADK_MODEL` to `gemini-2.5-flash`, or another model supported by your Google account and current ADK version.
6. Optional but recommended: set `AGENT_URL` to your public Vercel production URL, for example `https://your-project.vercel.app`.
7. Deploy, then verify `https://your-project.vercel.app/.well-known/agent-card.json`.

The public URL in the Agent Card is important because ServiceNow uses the card's `url` field for runtime A2A invocation. On Vercel, this app reads `AGENT_URL` first, then falls back to Vercel's deployment URL environment variables. Set `AGENT_URL` explicitly for the production domain that ServiceNow should call.

## Connect to ServiceNow

In the ServiceNow release/UI described in the referenced A2A article, configure an External AI Agent using the public Agent Card URL:
`https://your-project.vercel.app/.well-known/agent-card.json`

Use synchronous communication mode. Configure the execution Connection & Credential Alias according to your ServiceNow release and deployment requirements, then discover and test the agent.

## Important

- Never commit API keys or `.env` to Git.
- This starter does not query CMDB, create governance proposals, or update records.
- Add authentication, input validation, rate limits, logging, and monitoring before production.
- Confirm current Google ADK/A2A and ServiceNow release compatibility before production use.
