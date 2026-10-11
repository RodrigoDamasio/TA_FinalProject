"""Retrieval evaluation over the demo library (RAG_PLAN §3.9). 0 Gemini calls.

    python -m eval.retrieval_eval --embed fake        # smoke run (CI)
    python -m eval.retrieval_eval --embed onnx        # the real model (downloads once)

Writes eval/results/retrieval_<embed>.md with metrics per search mode and category."""

import argparse
import json
import tempfile
from pathlib import Path

from app.api.dependencies import demo_library_files
from app.application.documents import DocumentLimits, DocumentService
from app.application.embedding import CachedEmbeddings, Models, StaticModels
from app.application.indexing.chunkers import ChunkOptions, embed_text
from app.application.indexing.indexer import HybridIndexer
from app.application.retrieval.bm25 import ScopedBM25
from app.application.retrieval.evaluation import evaluate, summary
from app.application.retrieval.retriever import HybridRetriever
from app.domain.documents import DEMO_WORKSPACE
from app.infrastructure.document_store import SqliteDocumentStore
from app.infrastructure.embedding_cache import SqliteEmbeddingCache
from app.infrastructure.hashing_embedder import HashingEmbedder
from app.infrastructure.memory_vector_store import InMemoryVectorStore
from app.infrastructure.pdf_extract import PypdfExtractor

HERE = Path(__file__).resolve().parent
MODES = ("vector", "bm25", "hybrid")


def build(embed: str, max_chars: int, workdir: str, models_path: str):  # noqa: ANN201
    if embed == "onnx":
        from app.infrastructure.fastembed_models import OnnxModels

        models: Models = OnnxModels("BAAI/bge-small-en-v1.5", "", models_path, 2, 4)
        models.start()  # type: ignore[attr-defined]
        if not models.wait_ready(900):
            raise SystemExit("the embedding model did not load")
    else:
        models = StaticModels(HashingEmbedder())
    store = InMemoryVectorStore()
    bm25 = ScopedBM25(
        lambda s: ((c.id, embed_text(c), c.source_key, c.position) for c in store.iter_chunks(s))
    )
    embeddings = CachedEmbeddings(SqliteEmbeddingCache(f"{workdir}/cache.db"))
    indexer = HybridIndexer(store, models, embeddings, bm25, ChunkOptions(max_chars, 400, 0.15))
    service = DocumentService(
        SqliteDocumentStore(f"{workdir}/docs.db"), indexer, PypdfExtractor(), DocumentLimits()
    )

    class Inline:
        def submit(self, job_id: str) -> None:
            service.run_job(job_id)

        def has_capacity(self) -> bool:
            return True

    service.runner = Inline()
    service.schedule_seed(demo_library_files(str(HERE.parent / "samples" / "library")))
    return store, models, embeddings, bm25


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embed", choices=("fake", "onnx"), default="fake")
    parser.add_argument("--k", type=int, default=6)
    parser.add_argument("--max-chars", type=int, default=1400)
    parser.add_argument("--models-path", default="models")
    args = parser.parse_args()
    dataset = json.loads((HERE / "retrieval_dataset.json").read_text())
    lines = [
        f"# Retrieval evaluation ({args.embed} embedder, k={args.k}, chunks ≤ {args.max_chars})",
        "",
        "| Mode | Category | hit@k | MRR | P@k | R@k | nDCG@k |",
        "|---|---|---|---|---|---|---|",
    ]
    with tempfile.TemporaryDirectory() as workdir:
        store, models, embeddings, bm25 = build(
            args.embed, args.max_chars, workdir, args.models_path
        )
        for mode in MODES:
            retriever = HybridRetriever(store, models, embeddings, bm25, mode=mode)
            results = evaluate(dataset, retriever, [DEMO_WORKSPACE], args.k)
            for category, m in summary(results).items():
                lines.append(
                    f"| {mode} | {category} | {m['hit']} | {m['mrr']} | {m['precision']} | "
                    f"{m['recall']} | {m['ndcg']} |"
                )
            if mode == "hybrid":
                misses = [r for r in results if r.category != "negative" and r.hit == 0]
                lines += ["", f"Hybrid misses: {', '.join(r.id for r in misses) or 'none'}", ""]
    out = HERE / "results" / f"retrieval_{args.embed}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
