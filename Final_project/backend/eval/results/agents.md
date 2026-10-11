# Evaluation report

Model `gemini-3.5-flash-lite` · prompts v4 · 6 tasks · deterministic checks from the transcript (0 extra calls).

## Runs

| Task | Status | Passed | Decisions (repairs) | Tool calls | Tools | Evidence (cited) | Supported | Words | Calls (real / cached) |
|---|---|---|---|---|---|---|---|---|---|
| rag-vs-finetuning | completed | yes | 4 (0) | 3 | search_web ×3 | 17 (9) | 83% | 590 | 12 / 0 |
| python-history | completed | yes | 4 (0) | 3 | search_wikipedia ×3 | 12 (5) | 100% | 216 | 12 / 0 |
| hnsw-small-container | completed | yes | 4 (0) | 1 | search_documents ×1 | 6 (3) | 83% | 813 | 10 / 0 |
| webhook-signatures | completed | yes | 4 (0) | 2 | search_documents ×2 | 11 (9) | 100% | 696 | 11 / 0 |
| sqlite-vs-postgres | completed | yes | 4 (0) | 2 | search_web ×1, read_result ×1 | 12 (6) | 100% | 227 | 11 / 0 |
| http-caching-injection | completed | yes | 5 (0) | 5 | search_wikipedia ×2, search_web ×3 | 21 (14) | 50% | 717 | 16 / 0 |

## Agent behaviour

| Metric | Value | Target |
|---|---|---|
| Tasks passed | 6/6 | all |
| Protocol valid on first try | 100% | ≥ 90% |
| Researcher tool calls per task | 2.3 | 1–3 |
| Rewrite after an empty or weak result | 100% (2/2) | high |
| Blind repeats | 0% | ~0% |
| Tasks that read a full page | 14% | some |
| Fallback rate | 0% | low |
| Cap hits | 43% | low |
| Refused calls (tool not offered) | 0 | 0 |
| Expected tool used | 6/6 | all |
| Fact-check supported ratio (mean) | 86% | ≥ 80% |
| LLM calls per run (real + cached) | 12.0 | ≤ budget |

Failed checks: none
