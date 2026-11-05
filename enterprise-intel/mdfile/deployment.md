# Deployment

> This project runs locally with Docker. It has not been deployed to a cloud provider.

## Run locally

```bash
cp .env.example .env     # set JWT_SECRET, ANTHROPIC_API_KEY, GEMINI_API_KEY (and LANGFUSE_* if you want traces)
docker compose up --build
```

| What | Where |
|---|---|
| Frontend | http://localhost:3000 |
| API + Swagger | http://localhost:5500/docs |
| Postgres (pgvector) | localhost:5432, user and password `app` |

Local smoke test: register at /register → upload a document on Documents → wait for `ready` → ask a question in Chat (cited
answer) → ask for an analysis report (Agent trace shows search → analysis → report) → check Dashboard and Logs.

Useful commands: `docker compose run --rm backend pytest -q` (tests), `docker compose run --rm backend ruff check .` (lint),
`docker compose logs -f backend`, `docker compose down` (stop; add `-v` to also delete the database and uploads).
