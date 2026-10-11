# Research Assistant (v1.1): frontend

Next.js 16 UI of the final project's multi-agent research assistant. Ask a question, choose a **depth** and the **sources** (web, Wikipedia, documents), and watch the agents work live: the agent board, every **tool call** of the Researcher (query, provider, results, evidence range, weak / repeat / fallback / capped flags, the "↻ rewrote the query" marker), then the **structured report** with `[E<n>]` citation chips and references. A `/library` page manages this browser's uploads next to the read-only demo library. Design: [FRONTEND_PLAN.md](../FRONTEND_PLAN.md). API: [BACKEND_PLAN.md](../BACKEND_PLAN.md) §14 and the backend's `/docs`.

Started from the Module 5 frontend (copied, not imported); see FRONTEND_PLAN §2 for what was kept.

## Run locally

Node 24. `next dev` hits the file-watch limit on the user's machine, so use a production build:

```bash
# backend in fake mode (0 real calls), from ../backend:
LLM_MODE=fake EMBED_MODE=fake TELEGRAM_MODE=off FAKE_LLM_DELAY_S=0.3 \
  DATABASE_PATH=/tmp/fe.db CHROMA_PATH=/tmp/chroma FRONTEND_ORIGIN=http://localhost:3000 \
  .venv/bin/uvicorn app.main:app --port 8000          # or ../../../.venv/bin/uvicorn locally
# frontend:
npm ci
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run build && npm run start   # http://localhost:3000
```

## Tests (0 real AI calls)

```bash
npm run typecheck && npm run lint
npm run test:coverage        # Vitest + Testing Library: unit, API client and component tests
npm run test:e2e             # Playwright: starts the fake-mode backend and the production build
BASE_URL=https://<vercel-domain> npm run test:e2e   # production smoke (a real run only with E2E_REAL_RUN=1)
```

- `tests/fixtures/*.json` are **verbatim responses of a fake-mode backend** (`/health`, `/agents`, `POST /research`, `GET /research/{id}`, the SSE stream, `/documents`). `tests/fixtures.ts` parses them with the frontend's Zod schemas, so a backend shape change fails the suite.
- E2E covers: a full run with the live board and report, a documents-only run, upload → indexed → searched → deleted, approval, the 429 countdown, a sample replay, axe (no serious / critical) on home, run and library, and no horizontal scroll at 375 px.
- Playwright uses `/opt/pw-browsers/chromium` when present (cloud sessions), otherwise the installed Chrome; the backend interpreter is `../backend/.venv` or the shared `.venv` above the repo.

## Layout

| Folder | What |
|---|---|
| `lib/` | `api.ts` (fetch + Zod + `ApiError` from RFC 9457 slugs, `Retry-After`), `schemas.ts`, `events.ts` (SSE), `agents.ts` (board reducer), `tools.ts` (tool-card rules, rewrite marker), `citations.ts`, `report.ts`, `workspace.ts`, `thread.ts`, `replay.ts` |
| `hooks/` | `useRun` (SSE + reconcile + replay), `useHealth`, `useThread`, `useWorkspace`, `useApiError`, `useCountdown`, `useNow` |
| `components/activity` | `AgentBoard`, `ActivityFeed`, `ToolCallCard` |
| `components/report` | `Report`, `Markdown` (sanitized), `CitationChip` |
| `components/run` | `RunDetails`, `MetaBar`, `PerAgentTable`, `Deliveries` |
| `components/library` | `LibraryApp` |
| `components/chat` | `Composer`, `RunSettings`, `History`, `Suggestions`, `Turn` |

## Security

Untrusted text (reports, findings, snippets, titles, document names) is rendered as text or as Markdown with raw HTML skipped, images replaced by text and only `http(s)` links (`rel="noopener noreferrer nofollow"`). No `dangerouslySetInnerHTML`. The web UI never sends a `callback_url`. A CSP in `next.config.ts` limits `connect-src` to the API origin.
