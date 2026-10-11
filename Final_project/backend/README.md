# Backend — Multi-Agent Research Assistant

FastAPI backend of the final project. Design: [`../PLAN.md`](../PLAN.md) and [`../BACKEND_PLAN.md`](../BACKEND_PLAN.md) (shared contracts, file ownership, implementation order).

**Status:** step 1′ (foundation). This is the Module 5 backend, copied and renamed: `task` → `question`, `/run` → `/research`. The extensions (tools, RAG, webhooks) come next, as described in `BACKEND_PLAN.md` §20.

## Run the tests (0 real LLM calls)

```bash
# local (shared virtualenv one level above the repo)
../../../.venv/bin/python -m pytest -q --cov=app
../../../.venv/bin/ruff check . && ../../../.venv/bin/ruff format --check .

# cloud session (the SessionStart hook creates .venv here)
.venv/bin/python -m pytest -q --cov=app
```

## Run the API without Gemini

```bash
LLM_MODE=fake ../../../.venv/bin/uvicorn app.main:app --reload
# POST http://localhost:8000/research  {"question": "When should I use RAG instead of fine-tuning?"}
```

Opt-in suites that spend real quota: `-m live` (Gemini) and `-m live_wiki` (Wikipedia). **Only the user runs them, locally.**
