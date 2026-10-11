# Frontend — Plan

The web UI of the Research Assistant: ask a question, choose depth and sources, watch the agents work live (including **every tool call**), read the structured report, manage the document library, and replay sample runs at 0 calls. Big picture: [`PLAN.md`](PLAN.md) §2, §4. API contract: [`BACKEND_PLAN.md`](BACKEND_PLAN.md) §13–§15 and the live OpenAPI schema (`/docs`).

**Goal for the evaluator:** the UI makes the agent design *visible*: which tool the Researcher chose, the query it wrote, whether results were weak, when it rewrote the query, read a page, fell back or hit the cap, and which evidence each report sentence cites.

## 1. Stack and standards

| Item | Choice |
|---|---|
| Framework | **Next.js 16** (App Router), React 19, TypeScript strict. Read `node_modules/next/dist/docs/` before using an API (`searchParams` is a Promise) |
| Styling | Tailwind v4. Contrast: orange-700 / zinc-600 or darker on white (WCAG AA) |
| Data | `lib/api.ts` (fetch) + **Zod on every response**; `ApiError` kinds mapped from RFC 9457 slugs; `Retry-After` countdown |
| Live updates | `EventSource` on `/research/{id}/events` (SSE), reconnect with `Last-Event-ID`, then `GET /research/{id}` for the final state |
| Markdown | `react-markdown` + `remark-gfm`; no raw HTML; citation markers `[E<n>]` become reference chips |
| Config | `NEXT_PUBLIC_API_URL` (inlined at build time) |
| Tests | Vitest + Testing Library (coverage ≥ 80 %), Playwright E2E against the real backend in `LLM_MODE=fake`, axe (no serious/critical), no horizontal scroll at 375 px |
| Builds | `npm run build && npm run start` locally (`next dev` fails on the user's machine: file-watch limit) |
| Hosting | Vercel (Hobby), `vercel --prod` from the local machine |

## 2. Reuse (Module 5 `Lab_module5/frontend`)

| M5 part | Fate |
|---|---|
| `lib/api.ts`, `hooks/useApiError.ts`, `useCountdown.ts`, `useNow.ts`, `useHealth.ts`, `ErrorBanner`, `ServiceBanner`, `PrivacyNotice`, `Badge`, `Markdown` | **Kept**, schemas updated |
| `lib/schemas.ts`, `lib/events.ts` | **Rewritten** for the v1.1 view and events (§4) |
| `hooks/useRun.ts` (SSE + reconcile), `useThread.ts` + `lib/thread.ts` (local history) | Kept, extended with depth, sources and workspace |
| `chat/Composer`, `RunSettings`, `Suggestions`, `Turn` | Kept; `RunSettings` gains **depth** and **sources** |
| `activity/AgentBoard`, `ActivityFeed` | Kept; feed gains **tool-call cards** (§3.3) |
| `answer/FinalAnswer` | Replaced by **`report/Report`** (structured, §3.4) |
| `transcript/Transcript`, `WorkerData` | Kept; renders the new entry kinds |
| `ApprovalPanel`, `MetaBar`, `lib/replay.ts` (samples) | Kept; MetaBar shows tool calls, evidence, grounding |
| E2E `run.spec`, `samples.spec`, `production.spec` | Kept, updated selectors and flows |

Copied into `Final_project/frontend/` (never imported across repos). Node 24 (nvm locally, `/opt/node24` in the cloud).

## 3. Screens and components

### 3.1 Layout

```
┌──────────── header: title · service banner · Library link ───────────┐
│ history sidebar  │  thread (turns: question → live board → report)   │
│ (local, per      │  …                                                │
│  browser)        │  composer: question · depth · sources · options   │
└──────────────────┴───────────────────────────────────────────────────┘
```

At 375 px the sidebar collapses into a drawer; nothing scrolls horizontally (tables inside the report scroll in their own box).

### 3.2 Composer and run settings

- **Question** 10–2,000 chars (limits from `/health.limits`), Enter to send, Shift+Enter new line.
- **Depth**: brief · detailed · technical (segmented control, default `detailed`), with the word range from `/health` or the static profile table.
- **Sources**: checkboxes web · Wikipedia · documents (at least one). When search is unavailable (`/health.search.available=false`) the web box shows a note; it stays selectable (the backend reports the error or falls back if Wikipedia is also chosen).
- **Advanced** (collapsed): max iterations, require approval, use memory.
- **Workspace**: the browser's workspace id (`localStorage`, generated once) is sent with every run and upload, so `search_documents` sees the user's uploads plus the demo library.
- Submit → `POST /research` → 202 → open the SSE stream. 429/503 show the countdown from `Retry-After`.

### 3.3 Live activity: agent board and tool-call cards

- **Agent board**: one chip per agent (Supervisor, Researcher, Writer, Reviewer, Fact-Checker) with state idle · working · done · failed, from `agent_started` / `worker_result` / `decision` events.
- **Activity feed** (chronological):
  - Supervisor decision: action, target agent(s), task (collapsible), reason, "repaired" badge.
  - **Tool-call card** (the core visual): tool icon + name, the **query** or evidence id as written by the model, provider, results, evidence range (`E1–E6`), status badge (ok · empty · error · refused · capped), and flags: **weak**, **repeat**, **fallback → wikipedia**, duration.
  - **Rewrite marker**: when a search call follows an `empty`/`weak` one in the same task with a different query or tool, the card shows "↻ rewrote the query" (same rule as `rewrites_after_weak`).
  - Worker result: summary line (`data.summary` for the Researcher), collapsible findings.
  - Notices: unsourced, truncated, unknown_citation, cap_reached, possible_injection, degraded, forced.
- `evidence_added` updates an "Evidence: N" counter.

### 3.4 Report

From `report` (never re-parsed from markdown):
- Title, summary (lead), sections with headings; markdown body with GFM tables.
- **Citation chips** `[E3]` → hover/focus popover with the reference title, source kind icon, domain or `document p. N`, snippet; click scrolls to the reference.
- **References** list in order of first citation: kind icon, title, link (web/Wikipedia, `rel="noopener noreferrer nofollow"`, `target="_blank"`) or document name + page.
- Footer: depth, word count, grounding (`metadata.grounding`: supported / checked), forced/degraded banner, copy-as-markdown and download `.md`.
- **Approval** (`awaiting_approval`): draft preview + approve / reject with feedback (M5 `ApprovalPanel`), expiry countdown.

### 3.5 Run details (collapsible under the report)

- MetaBar: duration, LLM calls (real / cached), tool calls, evidence, tokens, estimated cost, models, prompt version.
- Per-agent table (`metadata.per_agent`).
- **Deliveries** (`GET /research/{id}/deliveries`, only when the run has a callback or Telegram channel): event, target host, status, attempts, last HTTP status/error, next attempt.
- Full transcript (M5 `Transcript`).

### 3.6 Document library (`/library`)

- Lists the workspace's documents (`GET /documents?workspace_id=…`) and the demo library (read-only, labeled "demo").
- Upload (`POST /documents`, multipart; `.md .txt .pdf`, ≤ 5 MB, ≤ 10 docs / 20 MB per workspace, from `/health.limits` when present): drag-and-drop or picker, per-file progress, then status polling (`queued → indexing → ready | failed`) every 2 s until terminal.
- Each row: name, type, pages, chunks, status badge, error reason, delete (`DELETE`, confirm dialog).
- Notice: uploads expire after 24 h; content goes to Gemini as data.

### 3.7 Samples (0 calls)

`GET /samples` → cards on the empty state; opening one replays it (M5 `lib/replay.ts`) through the same board and report components, labeled "sample run". In demo deployments this is the main way to see the agents without spending quota.

### 3.8 Service state

`ServiceBanner` from `/health`: service level (normal · degraded · unavailable with countdown), search and embedder status (`loading` → "the document index is warming up"), demo mode label when `llm_mode=fake`. Polled every 60 s and after any 503.

## 4. API contract used (Zod)

| Call | Schema |
|---|---|
| `POST /research` `{question, depth, sources, max_iterations?, require_approval?, use_memory?, workspace_id?}` → 202 | `{run_id, status, status_url, events_url, web_url}` |
| `GET /research/{id}` | `ResearchView`: `run_id, status, question, depth, sources[], channel, report \| null, metadata, conversation_history[], final_output, flags[], memory_used[], error, approval, sample` |
| `report` | `{title, summary, sections[{heading, content}], references[], depth, word_count}` |
| `conversation_history[]` | discriminated on `action`: `DELEGATE` · `FINAL` · `TOOL_CALL` (`tool, args, status, provider, fallback, results, evidence[], error, weak, repeat`) · `RESULT` (`task, content, evidence[], data`) · feedback · notice. Unknown actions are kept as a generic entry (forward compatible) |
| SSE `/research/{id}/events` | `run_started, agent_started, decision, tool_call, worker_result, evidence_added, notice, awaiting_approval, report_ready, completed, failed, cancelled` |
| `POST /research/{id}/approve` · `/reject {feedback}` | `ResearchView` |
| `GET /research/{id}/deliveries` | `[{id, kind, event, target_host, status, attempts, last_status, last_error, next_attempt_at, delivered_at}]` |
| `GET/POST/DELETE /documents` | document view (`id, name, media_type, size, pages, chunks, status, error, created_at, expires_at, demo`) |
| `GET /health` | `status, service_level, service_reason, retry_after_s, llm_mode, search{mode, available}, embedder{mode, status}, telegram{…}, outbox, models, prompt_version, usage_today, limits` |
| `GET /samples` · `/samples/{id}` | sample index · `ResearchView` with `sample=true` |
| Errors | `application/problem+json` → `ApiError{kind, status, title, detail, retryAfter}` |

Exact field lists are generated from `/openapi.json` during implementation and pinned in `tests/fixtures.ts` (a fixture per shape, taken from a fake-mode run). A shape change in the backend fails `tests/api.test.ts`.

## 5. Security and privacy

- Untrusted text (reports, findings, snippets, titles, document names) is rendered as **text or sanitized markdown only**; no `dangerouslySetInnerHTML`, no raw HTML in markdown, links only `http(s)`.
- The privacy notice states that questions, uploads and fetched pages are sent to Gemini and that search queries go to search engines.
- No secrets in the frontend; the callback secret returned by `POST /research` is never requested by the web UI (no `callback_url` from the browser).
- CSP via `next.config.ts` headers: `connect-src` the API origin only.

## 6. Tests

| Suite | Cases |
|---|---|
| **Unit** `lib.test.ts` | schemas parse every fixture; event reducer (each event type, out-of-order, reconnect dedup by event id); rewrite-marker rule; citation chip parsing (`[E3]`, `[E3][E5]`, unknown ids shown plain); evidence range formatting; local thread store |
| **Unit** `api.test.ts` | problem+json → `ApiError` kinds; `Retry-After` seconds/date; network failure; Zod failure → `contract` error |
| **Component** `app.test.tsx` | composer validation and settings; tool-call card variants (ok, empty, weak, repeat, fallback, capped, refused, error); report with chips and references; approval flow; library upload → polling → ready/failed → delete; service banner states; sample replay |
| **E2E** (Playwright, backend `LLM_MODE=fake`, real SSE) | full run with live board and report; sources = documents only (tool card shows `search_documents`); upload a `.md` and see it searched; sample replay; 429 countdown; axe on home, run, library; 375 px no horizontal scroll |
| **Production smoke** | `production.spec.ts` against the deployed URLs: health, one sample replay, (opt-in) one real run |

## 7. Deployment

- `vercel link` (project `research-assistant-final-project`), env `NEXT_PUBLIC_API_URL=https://<backend>.up.railway.app`, `vercel --prod`.
- Backend variables then set: `FRONTEND_ORIGIN` (CORS) and `FRONTEND_URL` (links in Telegram and callbacks), redeploy backend.
- Post-deploy: production smoke E2E, axe, Lighthouse accessibility ≥ 95.

## 8. Implementation steps

1. Copy the M5 frontend into `Final_project/frontend/`, update `package.json` name, remove M5-only parts, green build.
2. Fixtures from a fake-mode backend run + `lib/schemas.ts` / `lib/events.ts` rewrite; unit tests.
3. Composer: depth, sources, workspace id; `useThread` storage v2 (old entries ignored).
4. Activity feed with tool-call cards and rewrite markers.
5. Structured report with citation chips, references, approval.
6. Run details: MetaBar, per-agent, deliveries, transcript.
7. Library page (upload, polling, delete).
8. Samples and service banner updates.
9. E2E + axe + 375 px; coverage ≥ 80 %; lint and typecheck clean.
10. Deploy to Vercel, set backend CORS/URLs, production smoke. Record deviations here.
