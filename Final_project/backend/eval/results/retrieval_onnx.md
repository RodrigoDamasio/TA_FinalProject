# Retrieval evaluation (onnx embedder, k=6, chunks ≤ 1400)

| Mode | Category | hit@k | MRR | P@k | R@k | nDCG@k |
|---|---|---|---|---|---|---|
| vector | lookup | 0.857 | 0.69 | 0.262 | 0.786 | 0.687 |
| vector | all | 0.944 | 0.773 | 0.241 | 0.889 | 0.78 |
| vector | paraphrase | 1.0 | 0.75 | 0.233 | 1.0 | 0.825 |
| vector | keyword | 1.0 | 0.833 | 0.208 | 1.0 | 0.875 |
| vector | cross_doc | 1.0 | 1.0 | 0.25 | 0.75 | 0.807 |
| bm25 | lookup | 1.0 | 0.905 | 0.31 | 1.0 | 0.91 |
| bm25 | all | 0.833 | 0.796 | 0.213 | 0.778 | 0.772 |
| bm25 | paraphrase | 0.4 | 0.4 | 0.067 | 0.4 | 0.4 |
| bm25 | keyword | 1.0 | 1.0 | 0.208 | 1.0 | 1.0 |
| bm25 | cross_doc | 1.0 | 1.0 | 0.25 | 0.5 | 0.766 |
| hybrid | lookup | 1.0 | 0.786 | 0.286 | 0.929 | 0.807 |
| hybrid | all | 0.889 | 0.731 | 0.222 | 0.806 | 0.717 |
| hybrid | paraphrase | 0.6 | 0.433 | 0.133 | 0.6 | 0.438 |
| hybrid | keyword | 1.0 | 0.875 | 0.208 | 1.0 | 0.908 |
| hybrid | cross_doc | 1.0 | 1.0 | 0.25 | 0.5 | 0.722 |

Hybrid misses: r08, r12

