# Detailed plans: brief for the writing sessions

Seven detailed plans are written **in parallel**, one per cloud session. Each one designs one area of the backend **on top of the shared contracts** in [`../BACKEND_PLAN.md`](../BACKEND_PLAN.md) (the 🔒 sections).

| Task | File | Owner key in BACKEND_PLAN §4 |
|---|---|---|
| SUPERVISOR | `agents/SUPERVISOR_PLAN.md` | SUPERVISOR |
| RESEARCHER | `agents/RESEARCHER_PLAN.md` | RESEARCHER |
| WRITER | `agents/WRITER_PLAN.md` | WRITER |
| REVIEWER | `agents/REVIEWER_PLAN.md` (Reviewer + Fact-Checker) | REVIEWER |
| SEARXNG | `tools/SEARXNG_TOOL_PLAN.md` | SEARXNG |
| RAG | `tools/RAG_PLAN.md` | RAG |
| WEBHOOKS | `integrations/WEBHOOKS_PLAN.md` | WEBHOOKS |

## Rules for every session

1. **Documentation only.** Create **one file**: your plan, at the path above. Change nothing else, not even the other plans, `BACKEND_PLAN.md`, `PLAN.md` or `CLAUDE.md`.
2. **Read first:**
   - `CLAUDE.md`
   - `Final_project/BRAINSTORM.md`
   - `Final_project/PLAN.md`
   - `Final_project/BACKEND_PLAN.md`, all of it, especially the 🔒 sections and §4
   - your task's brief below
3. **Read the code you will reuse.** The backend isn't copied into this repo yet. Clone the public module repos **read-only** into `/tmp` and read the files your area adapts:
   - `git clone --depth 1 https://github.com/RodrigoDamasio/TA_Module5 /tmp/m5`, backend at `/tmp/m5/Lab_module5/backend/`
   - `git clone --depth 1 https://github.com/RodrigoDamasio/TA_Module4 /tmp/m4`, backend at `/tmp/m4/Lab_module4/backend/`
   - Cite reused files by their module path (e.g. `M5 application/workers/researcher.py`).
4. **Use the contracts, don't redefine them.** Refer to 🔒 types, ports, fields and names exactly as written. When you need something they don't provide, **don't invent a parallel version**: list it under **Contract gaps** (§10 of the template below).
5. **Stay inside your area.** If your design needs behavior from another area, state the assumption and list it under Contract gaps, or under Open questions if it's a design choice.
6. **Settled decisions are not reopened** (BRAINSTORM, PLAN §18). Concerns go under Open questions.
7. **Diagrams:**
   - at least one Mermaid diagram
   - no `;` inside sequence-diagram labels
   - try to render each one (`npx -y @mermaid-js/mermaid-cli -i x.mmd -o x.png`). If rendering isn't possible in the session, say so in the PR; the user's machine render-checks every diagram before merging
8. **Style:** match `PLAN.md` and `BACKEND_PLAN.md`. Plain English, tables for rules, short paragraphs, no filler. Aim for **200–450 lines**: complete enough to implement from without guessing, no longer.
9. **Finish:**
   - commit on a branch named `plan/<task-lowercase>`
   - push
   - open a **pull request to `main`** titled `Plan: <Task>`, whose description lists the contract gaps and open questions
   - if you can't open a PR, push the branch and say so

## Template (every plan uses these sections, in this order)

```
# <Area> — Detailed Plan
1. Scope and owned files        (copy the rows of BACKEND_PLAN §4 you own; what is in and out)
2. Reuse                        (M4/M5 files: as-is / adapted / dropped, and why)
3. Design                       (behavior, algorithms, data flow, prompts; the core of the plan)
4. Contracts used               (the 🔒 items you rely on, by name; no redefinition)
5. Diagrams                     (≥ 1 Mermaid)
6. Failure modes and edge cases (what can go wrong, what the code does)
7. Security                     (injection, SSRF, secrets, isolation, as relevant)
8. Cost                         (Gemini calls per run/task from this area; caching)
9. Tests                        (cases by suite; fakes used from BACKEND_PLAN §19.2; 0 real calls)
10. Contract gaps               (REQUIRED, "None" if none: needed change · why · smallest alternative)
11. Open questions              (decisions for the user, each with your recommendation)
12. Implementation steps        (ordered, small, each testable)
```

## Task briefs

### SUPERVISOR

Adapt M5 `supervisor.py`, `context.py`, `domain/protocol.py` and the `supervisor*.md` prompts:
- **New prompt inputs:** depth guidance (`PROFILES`), allowed `sources`, evidence count, and short **tool-use summaries** per Researcher result.
- **Drop** the `Topics:` convention.
- **`FINAL` = the full report Markdown,** passed through `parse_report`. When to delegate the Writer, Reviewer and Fact-Checker (parallel). Revision rules.
- **Forced completion and degradation** with the report format.
- **Memory:** recall of past reports (hybrid `MemoryStore` from RAG) and how they're shown to the Supervisor.
- **Approval:** web only. Telegram and API runs never pause.
- **Iteration and budget messages** in the prompt (budget `3 × iterations + 5`).
- **Deliverable:** a draft of the new prompts.

### RESEARCHER

The **centerpiece**: the evaluator looks here first.
- **Design the tool loop:** `ToolBox.offered()`, Gemini tool calling (M5 LLM types: `ToolSpec`, `ToolCall`, `ToolResultsMessage`), the cap of 3 enforced in code, then a final **no-tools** call.
- **Behavior on results:**
  - repeated identical queries are answered from the run's earlier results (0 cost)
  - empty or weak results → the prompt encourages rewriting the query
  - provider fallback is noted in the tool result
- **Exact layout of the tool-result text** (`[E<n>]` blocks, fenced).
- **Findings format:** Markdown citing `[E<n>]`, plus `evidence_ns`.
- **Recording and errors:** `ToolCallRecord` + `record_tool_call` (SSE `tool_call`), truncation and empty-answer handling, how `sources` limits the tools.
- **Deliverable:** a draft of the system prompt.
- **Explain why the design shows agentic behavior** (choosing tools, rewriting queries) and how the evaluation will measure it (PLAN §12).

### WRITER

- **Report Markdown format** per depth: `# Title`, a summary paragraph, `##` sections, `[E<n>]` citations, no Sources section (it is rebuilt). Write the **`guidance` texts** for the three `DepthProfile`s.
- **Writer context:** which evidence chunk texts are loaded through `Retriever.get` (the cited ones first, then the rest up to the budget), and how findings and Reviewer issues are included on revisions.
- **`parse_report`:** the full algorithm, every fallback, word counting and reference building (strings + `sources` objects), with many test cases.
- **Deliverable:** a draft of the Writer prompt.

### REVIEWER

- **Reviewer:** a checklist covering task fit, depth fit, structure, unsupported claims, balance and loaded language, and citation coverage. Strict/lenient schemas (keep or extend M5 `Review`).
- **Fact-Checker, redesigned around retrieval:**
  - the app extracts the cited claims (sentences with `[E<n>]`) **deterministically**
  - it retrieves the top evidence per claim from `run:<id>` (`Retriever.search` with `source_keys` or the cited chunks)
  - then **one** LLM call judges all claims (`supported` / `unsupported` / `contradicted` + evidence ids)
- Limits (max claims) and how the results reach the Supervisor.
- **Deliverable:** drafts of both prompts.

### SEARXNG

- **`searxng.py` adapter:** request, response mapping to `WebResult`, timeouts, `unresponsive_engines`, the `search_cache`, the fake.
- **Tools:** `search_web` (snippets → `SourceText` → `Indexer.index` in the run scope → `Retriever.search` in the run scope), `search_wikipedia` (wrap the M5 Wikipedia client), `read_result(evidence_id)` (fetch through `PageFetcher`, extract, index, retrieve).
- **`html_extract.py`:** choose the library (compare `trafilatura` with `readability-lxml` + `markdownify` on size, quality, license and dependencies), and handle non-HTML types.
- **`safe_http.py` SSRF guard in full:**
  - IP rules, including IPv6 and mapped addresses
  - connect to the resolved IP while keeping Host/SNI
  - redirects, size and time caps, allowed schemes and ports
  - it's shared with WEBHOOKS, so define its function signatures
- **Fallback rules** (SearXNG → Wikipedia), service level effects, and operating the engine list (from the POC).

### RAG

- **Chunkers:** M4 Markdown + fixed-size, adapted to `EvidenceChunk` and prose. PDF extraction with page mapping (`pypdf`). Duplicate removal by content hash.
- **Indexing:** the `Indexer` (embedding cache, batching, locking), the `Retriever` (vector + **BM25 per scope** + RRF, `source_keys` filter, optional reranker), BM25 rebuild at startup.
- **Documents:** `DocumentService` and the documents API:
  - validation by content
  - workspace lifecycle and quotas
  - the indexing job runner
  - purge on expiry
- **Demo library:** choose **5–8 permissively licensed** documents on AI engineering topics, plus **one with a planted prompt injection**. List each with source, license and size; `samples/library/LICENSES.md`.
- **Memory:** the hybrid `MemoryStore` (what gets embedded from a report, `recall`).
- **Retrieval evaluation:** a dataset of questions with known relevant chunks over the demo library, and metrics (M4).
- **RAM budget** within 1 GB (M4 `MEMORY_TUNING.md`).

### WEBHOOKS

- **Telegram inbound** (`api/webhooks.py`, `application/telegram.py`):
  - secret header, chat allowlist, `update_id` duplicate check (`UpdateLedger`)
  - commands `/start`, `/help`, text = a question (depth `brief`)
  - per-chat rate limit
  - replies through the outbox, with the exact message texts
- **The `setWebhook` command** the user runs. The token is never in the repo.
- **Outbound callback:**
  - Standard Webhooks signing (with test vectors)
  - a **receiver verification example** (Python) for the docs and the demo
  - `callback_url` validation using `safe_http`
- **Outbox dispatcher:** claiming due rows, the retry schedule, Telegram `retry_after`, `dead`, the deliveries view, the sweeper, startup recovery, and why delivery is at-least-once.
- **Demo:** how to show a callback delivery live (e.g. a public request-inspector service, or a tiny local receiver plus a tunnel). Recommend one.
