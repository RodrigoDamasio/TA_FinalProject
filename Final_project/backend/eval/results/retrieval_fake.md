# Retrieval evaluation (fake embedder, k=6, chunks ≤ 1400)

| Mode | Category | hit@k | MRR | P@k | R@k | nDCG@k |
|---|---|---|---|---|---|---|
| vector | lookup | 0.429 | 0.262 | 0.167 | 0.429 | 0.322 |
| vector | all | 0.444 | 0.238 | 0.111 | 0.389 | 0.261 |
| vector | paraphrase | 0.2 | 0.05 | 0.033 | 0.2 | 0.086 |
| vector | keyword | 0.5 | 0.175 | 0.083 | 0.5 | 0.254 |
| vector | cross_doc | 1.0 | 0.75 | 0.167 | 0.5 | 0.5 |
| bm25 | lookup | 1.0 | 0.905 | 0.31 | 1.0 | 0.91 |
| bm25 | all | 0.833 | 0.796 | 0.213 | 0.778 | 0.772 |
| bm25 | paraphrase | 0.4 | 0.4 | 0.067 | 0.4 | 0.4 |
| bm25 | keyword | 1.0 | 1.0 | 0.208 | 1.0 | 1.0 |
| bm25 | cross_doc | 1.0 | 1.0 | 0.25 | 0.5 | 0.766 |
| hybrid | lookup | 1.0 | 0.576 | 0.286 | 1.0 | 0.683 |
| hybrid | all | 0.833 | 0.585 | 0.194 | 0.778 | 0.616 |
| hybrid | paraphrase | 0.4 | 0.25 | 0.067 | 0.4 | 0.286 |
| hybrid | keyword | 1.0 | 0.812 | 0.167 | 1.0 | 0.858 |
| hybrid | cross_doc | 1.0 | 1.0 | 0.25 | 0.5 | 0.722 |

Hybrid misses: r08, r11, r12

