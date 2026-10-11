# Backend — Multi-Agent Research Assistant

FastAPI backend of the final project. Design: [`../PLAN.md`](../PLAN.md) and [`../BACKEND_PLAN.md`](../BACKEND_PLAN.md) (shared contracts, file ownership, implementation order).

**Status:** step 3 done, tag `contracts-v1`. Every shared contract is code, and each area has a working **stub**, so a full `LLM_MODE=fake` run goes question → tool calls → evidence → report → signed callback. The areas are implemented next (`BACKEND_PLAN.md` §20 step 4, stubs listed in §25).

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
