# Evaluation report

Model `demo` · prompts v5 · 8 tasks · deterministic checks from the transcript (0 extra calls).

## Runs

| Task | Status | Passed | Decisions (repairs) | Tool calls | Tools | Evidence (cited) | Supported | Words | Calls (real / cached) |
|---|---|---|---|---|---|---|---|---|---|
| rag-vs-finetuning | completed | NO | 4 (0) | 1 | search_web ×1 | 3 (3) | — | 667 | 0 / 8 |
| python-history | completed | yes | 4 (0) | 1 | search_wikipedia ×1 | 3 (3) | — | 225 | 0 / 8 |
| hnsw-small-container | completed | NO | 4 (0) | 1 | search_documents ×1 | 0 (0) | — | 902 | 0 / 8 |
| webhook-signatures | completed | NO | 4 (0) | 1 | search_web ×1 | 3 (3) | — | 667 | 0 / 8 |
| sqlite-vs-postgres | completed | yes | 4 (0) | 1 | search_web ×1 | 3 (3) | — | 225 | 0 / 8 |
| http-caching-injection | completed | yes | 4 (0) | 1 | search_web ×1 | 3 (3) | — | 667 | 0 / 8 |
| rag-followup-cost/1 | completed | yes | 4 (0) | 1 | search_web ×1 | 3 (3) | — | 225 | 0 / 8 |
| rag-followup-cost/2 | completed | yes | 4 (0) | 1 | search_conversation ×1 | 3 (3) | — | 225 | 0 / 8 |

## Agent behaviour

| Metric | Value | Target |
|---|---|---|
| Tasks passed | 5/8 | all |
| Protocol valid on first try | 100% | ≥ 90% |
| Researcher tool calls per task | 1.0 | 1–3 |
| Rewrite after an empty or weak result | n/a (0/0) | high |
| Blind repeats | 0% | ~0% |
| Tasks that read a full page | 0% | some |
| Fallback rate | 0% | low |
| Cap hits | 0% | low |
| Refused calls (tool not offered) | 0 | 0 |
| Expected tool used | 7/8 | all |
| Fact-check supported ratio | n/a | ≥ 80% |
| LLM calls per run (real + cached) | 8.0 | ≤ budget |

Failed checks: rag-vs-finetuning: on_topic; hnsw-small-container: citations_valid; webhook-signatures: expected_tool_used

## Follow-ups

| Turn | Context turns | Standalone TASK | search_conversation | Evidence reused / new | Query repeat rate |
|---|---|---|---|---|---|
| rag-followup-cost/2 | 1 | yes | used | 3 / 0 | 0% |

Follow-ups that used search_conversation: 1/1.
