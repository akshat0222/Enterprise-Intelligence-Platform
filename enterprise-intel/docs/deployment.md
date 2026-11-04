# Deployment

Target: database on **Supabase** (or Neon), backend on **Render** (Docker), frontend on **Vercel**. Nothing here has been
deployed yet; these are the steps and the pitfalls found while building.

## 1. Database

1. Create a Supabase/Neon project. Copy the connection string (`postgresql://…`). The backend rewrites it to the
   `postgresql+psycopg://` form automatically.
2. The first backend start runs `alembic upgrade head`, which executes `CREATE EXTENSION IF NOT EXISTS vector`.
   If your plan blocks that, enable the `vector` extension in the provider's dashboard first.

## 2. Backend on Render

`render.yaml` at the repository root defines the service (Docker, root `enterprise-intel/backend`, health check `/health`).

| Variable | Value |
|---|---|
| `ENVIRONMENT` | `production` (the app refuses to start with a weak `JWT_SECRET`; cookies become `Secure; SameSite=None`) |
| `DATABASE_URL` | from step 1 |
| `JWT_SECRET` | long random value (the blueprint generates one) |
| `CORS_ORIGINS` | the exact Vercel URL, e.g. `https://your-app.vercel.app` |
| `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` | provider keys |
| `EMBEDDING_PROVIDER` | `local` (default) or `openai` |
| `MIN_SIMILARITY` | `0.4` (tuned for the local MiniLM embedder; retune if you change embedder) |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | optional; host must match the key region (EU `https://cloud.langfuse.com`, US `https://us.cloud.langfuse.com`) |
| `UPLOAD_DIR` | `/data/uploads` (mounted disk) |

Gotchas:
- **Memory:** the local embedding model plus the optional reranker need roughly 1 GB RAM. The free 512 MB instance is too
  small; use a paid instance, or set `EMBEDDING_PROVIDER=openai` (needs `OPENAI_API_KEY` with credit; vectors are shortened to
  384 dimensions to match the schema).
- **Disk:** uploaded originals live under `UPLOAD_DIR` and are only needed to reprocess a document. Without a persistent disk
  they vanish on restart and "Reprocess" will fail.
- **Free instances sleep:** hit `/health` before a demo.
- **Upload size:** keep any proxy body-size limit above `MAX_UPLOAD_MB` (default 20).
- **Changing the embedding model** invalidates stored vectors; reprocess every document.

## 3. Frontend on Vercel

1. Import the repository; set **Root Directory** to `enterprise-intel/frontend`.
2. Environment variable `NEXT_PUBLIC_API_URL` = the Render URL (no trailing slash).
3. Deploy, then put the Vercel URL into the backend's `CORS_ORIGINS` and redeploy the backend.

## 4. Smoke test after every deploy

register → upload a PDF → wait for `ready` → ask a question (cited answer) → ask for an analysis report (agent trace shows
search → analysis → report) → check the dashboard and Langfuse.

```bash
curl https://<backend>/health
```

## Local (Docker)

```bash
cp .env.example .env   # set JWT_SECRET and API keys
docker compose up --build
```
Frontend http://localhost:3000, API http://localhost:5500/docs.
