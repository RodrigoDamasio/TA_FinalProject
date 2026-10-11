"""Layering and security guards (Q4), as in Labs 1–4."""

import ast
import subprocess
import sys
from pathlib import Path

from app.application.prompts import PromptLibrary, placeholders

APP = Path(__file__).resolve().parents[1] / "app"
LIBRARIES = (
    "google",
    "sqlite3",
    "httpx",
    "httpcore",
    "chromadb",
    "fastembed",
    "pypdf",
    "trafilatura",
    "subprocess",
)
WEB_AND_IO = {"fastapi", "starlette", *LIBRARIES}


def imported_modules(path: Path) -> set[str]:
    modules = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def offenders(layer: str, forbidden: set[str]) -> set[str]:
    return {
        f"{p.relative_to(APP)} imports {m}"
        for p in (APP / layer).rglob("*.py")
        for m in imported_modules(p)
        if any(m == f or m.startswith(f + ".") for f in forbidden)
    }


def test_domain_imports_only_stdlib_and_pydantic():
    inner = {"app.application", "app.infrastructure", "app.api", "app.config"}
    assert offenders("domain", WEB_AND_IO | inner) == set()


def test_application_never_touches_web_storage_or_providers():
    assert offenders("application", WEB_AND_IO | {"app.infrastructure", "app.api"}) == set()


def test_infrastructure_never_imports_the_api():
    assert offenders("infrastructure", {"fastapi", "starlette", "app.api"}) == set()


def test_libraries_stay_in_their_modules():
    where: dict[str, set[str]] = {lib: set() for lib in LIBRARIES}
    for path in APP.rglob("*.py"):
        for module in imported_modules(path):
            for lib in LIBRARIES:
                if module == lib or module.startswith(lib + "."):
                    where[lib].add(str(path.relative_to(APP)))
    allowed = {
        "google": {"infrastructure/gemini_client.py"},
        "sqlite3": {"infrastructure/database.py"},
        # BACKEND_PLAN §19.3 (the Gemini adapter catches httpx timeouts raised by the SDK)
        "httpx": {
            "infrastructure/gemini_client.py",
            "infrastructure/wikipedia.py",
            "infrastructure/searxng.py",
            "infrastructure/safe_http.py",
            "infrastructure/telegram_client.py",
            "infrastructure/webhook_sender.py",
        },
        "httpcore": {"infrastructure/safe_http.py"},
        "chromadb": {"infrastructure/chroma_store.py"},
        "fastembed": {"infrastructure/fastembed_models.py"},
        "pypdf": {"infrastructure/pdf_extract.py"},
        "trafilatura": {"infrastructure/html_extract.py"},
        "subprocess": set(),  # nothing is ever executed
    }
    for lib, files in where.items():
        assert files <= allowed[lib], (lib, files - allowed[lib])
    assert where["google"] == allowed["google"] and where["sqlite3"] == allowed["sqlite3"]


def test_pages_are_never_fetched_outside_the_ssrf_guard():
    """SEARXNG_TOOL_PLAN §3.8: trafilatura only extracts; its own downloader bypasses the guard."""
    names = {"fetch_url", "fetch_response"}
    used = {
        f"{p.relative_to(APP)}: {node.attr if isinstance(node, ast.Attribute) else node.id}"
        for p in APP.rglob("*.py")
        for node in ast.walk(ast.parse(p.read_text()))
        if (isinstance(node, ast.Attribute) and node.attr in names)
        or (isinstance(node, ast.Name) and node.id in names)
    }
    imported = {
        f"{p.relative_to(APP)}: {alias.name}"
        for p in APP.rglob("*.py")
        for node in ast.walk(ast.parse(p.read_text()))
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("trafilatura")
        for alias in node.names
    }
    assert used == set() and imported == set()


def test_workers_reach_evidence_only_through_tools_and_the_retriever():
    """BACKEND_PLAN §19.3: no worker imports indexing, retrieval or a vector store."""
    banned = {
        "app.application.indexing",
        "app.application.retrieval",
        "app.infrastructure",
        "chromadb",
        "fastembed",
    }
    assert offenders("application/workers", banned) == set()


def test_sql_lives_only_in_the_sqlite_adapters():
    sql_words = ("SELECT ", "INSERT ", "UPDATE ", "DELETE FROM")
    files = {
        str(p.relative_to(APP))
        for p in APP.rglob("*.py")
        if any(w in p.read_text() for w in sql_words)
    }
    assert files <= {
        "infrastructure/database.py",
        "infrastructure/sqlite_store.py",
        "infrastructure/document_store.py",  # RAG (BACKEND_PLAN §4)
        "infrastructure/embedding_cache.py",  # RAG
    }


def test_model_output_is_never_executed():
    for path in APP.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"exec", "eval", "compile", "__import__"}, path


def test_ruff_flags_sql_built_from_strings(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def f(c, x):\n    return c.execute(f\"SELECT * FROM t WHERE a = '{x}'\")\n")
    result = subprocess.run(  # noqa: S603 — fixed argv
        [sys.executable, "-m", "ruff", "check", "--no-cache", "--select", "S608", str(bad)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0 and "S608" in result.stdout


def test_prompt_files_use_only_known_placeholders():
    library = PromptLibrary()
    known = {
        "persona", "canary", "worker_list", "parallel_rule", "fact_check_hint", "question",
        "memories", "sources", "evidence", "tools", "cap", "known_evidence", "guidance",
        "transcript", "iterations_left", "last_hint", "parser_error", "task", "notes",
        "review", "fact_check", "previous_draft", "draft", "sentences", "validation_errors",
        "depth_hint", "word_limit", "tool_log", "earlier_turns",
    }  # fmt: skip
    root = APP / "prompts"
    for path in root.rglob("*.md"):
        text = library.read(str(path.relative_to(root)))
        assert placeholders(text) <= known, path
        if path.parent.name == "workers" and path.stem.endswith("_task"):
            assert "{question}" in text or "{task}" in text, path
    assert library.version == "5"
