# Evaluation report

Model `demo` · prompts v3 · 6 tasks · deterministic checks from the transcript (0 extra calls).

## Runs

| Task | Status | Passed | Decisions (repairs) | Tool calls | Tools | Evidence (cited) | Supported | Words | Calls (real / cached) |
|---|---|---|---|---|---|---|---|---|---|
| rag-vs-finetuning | completed | NO | 4 (0) | 1 | search_web ×1 | 3 (3) | — | 667 | 8 / 0 |
| python-history | completed | yes | 4 (0) | 1 | search_wikipedia ×1 | 3 (3) | — | 225 | 8 / 0 |
| hnsw-small-container | completed | yes | 4 (0) | 1 | search_documents ×1 | 6 (3) | — | 902 | 8 / 0 |
| webhook-signatures | completed | NO | 4 (0) | 1 | search_web ×1 | 3 (3) | — | 667 | 8 / 0 |
| sqlite-vs-postgres | completed | yes | 4 (0) | 1 | search_web ×1 | 3 (3) | — | 225 | 8 / 0 |
| http-caching-injection | completed | yes | 4 (0) | 1 | search_web ×1 | 3 (3) | — | 667 | 8 / 0 |

## Agent behaviour

| Metric | Value | Target |
|---|---|---|
| Tasks passed | 4/6 | all |
| Protocol valid on first try | 100% | ≥ 90% |
| Researcher tool calls per task | 1.0 | 1–3 |
| Rewrite after an empty or weak result | n/a (0/0) | high |
| Blind repeats | 0% | ~0% |
| Tasks that read a full page | 0% | some |
| Fallback rate | 0% | low |
| Cap hits | 0% | low |
| Refused calls (tool not offered) | 0 | 0 |
| Expected tool used | 5/6 | all |
| Fact-check supported ratio | n/a | ≥ 80% |
| LLM calls per run (real + cached) | 8.0 | ≤ budget |

Failed checks: rag-vs-finetuning: on_topic; webhook-signatures: expected_tool_used
