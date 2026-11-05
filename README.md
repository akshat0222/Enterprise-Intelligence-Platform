# Enterprise Intelligence Platform

Multi-user RAG and AI agent platform: document upload, hybrid retrieval with cited answers, a LangGraph agent, conversation
memory, a dashboard, and an evaluation harness. Runs locally with Docker.

The project lives in [`enterprise-intel/`](enterprise-intel/). Start with [its README](enterprise-intel/README.md).

```bash
cd enterprise-intel
cp .env.example .env     # add your API keys
docker compose up --build
```

Frontend http://localhost:3000, API docs http://localhost:5500/docs.
