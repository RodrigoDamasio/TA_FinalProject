"""The retrieval dataset resolves against the real chunked library, and the metrics."""

import json
from pathlib import Path

from fakes import rag_stack

from app.api.dependencies import demo_library_files
from app.application.documents import DocumentLimits, DocumentService
from app.application.retrieval.evaluation import (
    evaluate,
    hit_at_k,
    matches,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    summary,
)
from app.domain.documents import DEMO_WORKSPACE
from app.infrastructure.document_store import SqliteDocumentStore
from app.infrastructure.pdf_extract import PypdfExtractor

DATASET = json.loads((Path(__file__).parents[2] / "eval" / "retrieval_dataset.json").read_text())


def test_metrics():
    assert reciprocal_rank([False, True]) == 0.5 and hit_at_k([False, True], 1) == 0.0
    assert recall_at_k([1, None], 3) == 0.5 and recall_at_k([], 3) == 0.0
    assert ndcg_at_k([True], 3, 1) == 1.0 and ndcg_at_k([], 3, 0) == 0.0


def test_every_target_resolves_in_the_chunked_library(tmp_path):
    categories = {item["category"] for item in DATASET}
    assert categories == {"lookup", "paraphrase", "keyword", "cross_doc", "negative"}
    indexer, retriever, store, _ = rag_stack(1400, 400)
    service = DocumentService(
        SqliteDocumentStore(str(tmp_path / "d.db")), indexer, PypdfExtractor(), DocumentLimits()
    )

    class Inline:
        def submit(self, job_id):
            service.run_job(job_id)

        def has_capacity(self):
            return True

    service.runner = Inline()
    service.schedule_seed(demo_library_files("samples/library"))
    chunks = list(store.iter_chunks(DEMO_WORKSPACE))
    for item in DATASET:
        for target in item["relevant"]:
            assert any(matches(c, target) for c in chunks), (item["id"], target)
    results = evaluate(DATASET, retriever, [DEMO_WORKSPACE], 6)
    overall = summary(results)["all"]
    assert overall["hit"] >= 0.5  # smoke floor with the hashing fake (the ONNX run is better)
