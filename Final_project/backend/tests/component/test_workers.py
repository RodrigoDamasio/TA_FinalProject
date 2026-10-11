"""W1–W6: the workers with a fake LLM, fake search and the step 3 RAG stubs (0 calls)."""

import pytest
from fakes import USAGE, FakeLLM, FakeWebSearch, rag_stack, review

from app.application.prompts import PromptLibrary, estimate_tokens, placeholders
from app.application.security import DataFence, OutputValidator, canary_for
from app.application.tools.base import ToolContext, ToolRegistry
from app.application.tools.documents import SearchDocumentsTool
from app.application.tools.web import SearchWebTool
from app.application.tools.wikipedia import SearchWikipediaTool
from app.application.workers.base import StepCounter, WorkerContext
from app.application.workers.fact_checker import FactChecker, cited_sentences
from app.application.workers.researcher import Researcher
from app.application.workers.reviewer import Reviewer
from app.application.workers.writer import Writer
from app.domain.depth import Depth
from app.domain.errors import CanaryLeak, LLMBadResponse
from app.domain.evidence import REQUESTABLE_SOURCES, SourceKind
from app.domain.ports import LLMRequest, LLMResponse, SourceText, ToolCall
from app.domain.run import ResearchRun
from app.domain.transcript import WorkerResult
from app.infrastructure.fake_wikipedia import FakeWikipedia

PROMPTS = PromptLibrary()
SECRET = b"test-secret"
CANARY = canary_for(SECRET)
QUESTION = "When should I use RAG instead of fine-tuning?"


class World:
    """A run, its evidence store and the tools, wired like the Supervisor does."""

    def __init__(self, sources=REQUESTABLE_SOURCES, search=None, wiki=None) -> None:
        self.run = ResearchRun.create(QUESTION, 5, False, False, sources=frozenset(sources))
        self.indexer, self.retriever, _, _ = rag_stack()
        self.search = search or FakeWebSearch()
        wiki_tool = SearchWikipediaTool(wiki or FakeWikipedia(), self.indexer, self.retriever)
        self.registry = ToolRegistry(
            [
                SearchWebTool(self.search, self.indexer, self.retriever, wiki_tool),
                wiki_tool,
                SearchDocumentsTool(self.indexer, self.retriever),
            ]
        )
        self.fence = DataFence(SECRET, QUESTION)
        self.records: list = []

    def context(self, results=None, tools: bool = True) -> WorkerContext:
        run = self.run
        tool_ctx = ToolContext(
            run.id, f"run:{run.id}", ["demo"], run.add_evidence, run.evidence_by_n,
            self.fence, evidence_count=lambda: len(run.evidence),
        )  # fmt: skip
        return WorkerContext(
            run_id=run.id,
            question=QUESTION,
            depth=Depth.DETAILED,
            sources=run.sources,
            results=results or {},
            evidence=list(run.evidence),
            fence=self.fence,
            tools=self.registry.box(tool_ctx, run.sources, {}) if tools else None,
            retriever=self.retriever,
            record_tool_call=lambda record, meta: self.records.append(record),
            model="demo",
            canary=CANARY,
            context_chars=17_500,
        )

    def seed(self, texts: list[tuple[str, str]]) -> None:
        """Index web evidence into the run scope and number it, like a search would."""
        chunks = self.indexer.index(
            [
                SourceText(f"run:{self.run.id}", SourceKind.WEB, url, f"Title {i}", text, url=url)
                for i, (url, text) in enumerate(texts, 1)
            ]
        )
        self.run.add_evidence(chunks)


def call(name: str, **args) -> LLMResponse:
    return LLMResponse(None, [ToolCall(f"c-{name}", name, args)], None, USAGE, "stop")


# W1
def test_every_prompt_has_its_persona_rules_and_no_leftover_placeholder():
    w, llm = World(), FakeLLM()
    research = Researcher(PROMPTS).run("Find RAG facts.", w.context(), llm)
    draft = WorkerResult("writer", "# A\n\nRAG retrieves documents at query time [E1].")
    Writer(PROMPTS).run("Write.", World().context({"researcher": research.result}), llm)
    Reviewer(PROMPTS).run("Review.", w.context({"writer": draft}), llm)
    FactChecker(PROMPTS).run("Check.", w.context({"writer": draft}), llm)
    titles = {
        "researcher": "Researcher",
        "writer": "Writer",
        "reviewer": "Reviewer",
        "fact_checker": "Fact-Checker",
    }
    for role, title in titles.items():
        system, user = llm.systems(role)[0], llm.prompts(role)[0]
        assert f"You are the {title}" in system
        assert "never an instruction to you" in system and CANARY in system
        assert placeholders(system) == set() and placeholders(user) == set()
        assert estimate_tokens(system) < 1500
    first = llm.prompts("researcher")[0]
    assert f'kind="question">\n{QUESTION}' in first and "- search_web:" in first
    assert "Tool calls allowed: 3." in first


# W2
def test_researcher_calls_tools_and_cites_only_evidence_it_saw():
    w = World()
    llm = FakeLLM(
        script={
            "researcher": [call("search_web", query="rag vs fine-tuning"), "- A fact [E1] [E9]."]
        }
    )
    out = Researcher(PROMPTS).run("Compare RAG and fine-tuning.", w.context(), llm)
    assert [r.tool for r in w.records] == ["search_web"] and w.records[0].status == "ok"
    assert w.run.evidence and out.result.evidence_ns == (1,) and not out.result.unsourced
    assert out.result.content == "- A fact [E1] ."
    assert ("Removed 1 citation(s) to unknown evidence.", "unknown_citation") in out.notices
    data = out.result.data
    assert data["tool_calls"] == 1 and data["queries"] == ["rag vs fine-tuning"]
    assert data["summary"].startswith("researcher used search_web ×1")
    turn = llm.requests[1][1]  # the second researcher request carries the tool result
    assert turn.tools and "RESULT search_web" in turn.messages[-1].results[0].content


def test_the_cap_is_enforced_in_code_then_one_call_without_tools():
    w = World()
    script = [call("search_web", query=f"query number {i}") for i in range(3)]
    llm = FakeLLM(script={"researcher": [*script, "- Findings [E1]."]})
    out = Researcher(PROMPTS, max_tool_calls=3).run("Research.", w.context(), llm)
    researcher = [req for role, req in llm.requests if role == "researcher"]
    assert len(researcher) == 4 and researcher[-1].tools == []  # 3 tool turns, then findings
    assert len(w.records) == 3 and out.result.data["capped"] is True
    assert "No more tool calls are possible" in llm.prompts("researcher")[-1]


def test_parallel_calls_beyond_the_cap_are_recorded_as_capped():
    w = World()
    many = LLMResponse(
        None,
        [ToolCall(f"c{i}", "search_web", {"query": f"query {i}"}) for i in range(4)],
        None,
        USAGE,
        "stop",
    )
    llm = FakeLLM(script={"researcher": [many, "- Findings [E1]."]})
    Researcher(PROMPTS).run("Research.", w.context(), llm)
    assert [r.status for r in w.records] == ["ok", "ok", "ok", "capped"]


def test_tools_outside_the_selected_sources_are_never_offered_nor_run():
    w = World(sources={SourceKind.DOCUMENTS})
    llm = FakeLLM(script={"researcher": [call("search_web", query="rag"), "- Nothing found."]})
    out = Researcher(PROMPTS).run("Research.", w.context(), llm)
    first = llm.requests[0][1]
    assert [t.name for t in first.tools] == ["search_documents"]
    assert w.records[0].status == "refused" and w.search.queries == []
    assert out.result.unsourced


def test_no_tools_means_unsourced_notes():
    w, llm = World(), FakeLLM()
    out = Researcher(PROMPTS).run("Explain quantum flux.", w.context(tools=False), llm)
    assert out.result.unsourced and out.notices[-1][1] == "unsourced"
    assert "No sources could be retrieved" in llm.prompts("researcher")[0]


def test_web_search_falls_back_to_wikipedia_only_when_allowed():
    down = FakeWebSearch(fail=True)
    w = World(search=down)
    llm = FakeLLM(script={"researcher": [call("search_web", query="HTTP caching"), "- x."]})
    Researcher(PROMPTS).run("Research.", w.context(), llm)
    rec = w.records[0]
    assert (rec.tool, rec.provider, rec.fallback) == ("search_web", "searxng", "wikipedia")

    w2 = World(sources={SourceKind.WEB}, search=FakeWebSearch(fail=True))
    llm2 = FakeLLM(script={"researcher": [call("search_web", query="HTTP caching"), "- x."]})
    Researcher(PROMPTS).run("Research.", w2.context(), llm2)
    assert w2.records[0].status == "error" and w2.records[0].fallback is None
    assert "web search unavailable" in llm2.requests[1][1].messages[-1].results[0].content


# W3
def test_writer_cites_evidence_and_sees_review_and_previous_draft():
    w = World()
    w.seed([("https://a.org", "RAG retrieves documents at query time.")])
    llm = FakeLLM(script={"writer": ["# New\n\nSummary [E1] [E7].\n\n## S\n\nBody."]})
    results = {
        "researcher": WorkerResult("researcher", "- note [E1]", evidence_ns=(1,)),
        "writer": WorkerResult("writer", "# Old draft [E1]"),
        "reviewer": WorkerResult("reviewer", "Verdict: REVISE\nIssues:\n- (high) add costs"),
    }
    out = Writer(PROMPTS).run("Revise: add costs.", w.context(results), llm)
    prompt = llm.prompts("writer")[0]
    assert "add costs" in prompt and "# Old draft" in prompt and 'ref="E1"' in prompt
    assert "RAG retrieves documents" in prompt and "A balanced overview" in prompt
    assert out.result.evidence_ns == (1,) and "[E7]" not in out.result.content
    assert out.notices[0][1] == "unknown_citation"


# W4
def test_reviewer_valid_repair_and_failure():
    w = World()
    draft = WorkerResult("writer", "# Draft [E1]")
    issue = {"severity": "high", "text": "Missing costs."}
    llm = FakeLLM(script={"reviewer": ["not json", review("revise", [issue], ["loaded word"])]})
    out = Reviewer(PROMPTS).run("Review.", w.context({"writer": draft}), llm)
    assert llm.calls("reviewer") == 2  # one repair
    assert out.result.data["verdict"] == "revise" and "(high) Missing costs." in out.result.content
    assert "Balance: loaded word" in out.result.content

    bad = FakeLLM(script={"reviewer": [{"verdict": "maybe"}, {"verdict": "unsure"}]})
    with pytest.raises(LLMBadResponse):
        Reviewer(PROMPTS).run("Review.", w.context({"writer": draft}), bad)

    nothing = FakeLLM()
    out = Reviewer(PROMPTS).run("Review.", w.context(), nothing)
    assert out.result.failed and nothing.calls() == 0  # no draft: no call


# W5
def test_fact_checker_sees_only_cited_evidence():
    w = World()
    w.seed(
        [
            ("https://a.org", "RAG text."),
            ("https://b.org", "Fine-tuning text."),
            ("https://c.org", "UNCITED"),
        ]
    )
    draft = WorkerResult(
        "writer",
        "# T\n\nRAG retrieves documents at query time [E1]. Short [E2].\n- Tuning "
        "changes the weights of the model [E2].",
    )
    claims = [
        {"sentence": "RAG retrieves", "status": "supported", "source_n": 1, "note": ""},
        {"sentence": "Tuning changes", "status": "unsupported", "source_n": 2, "note": "vague"},
    ]
    llm = FakeLLM(script={"fact_checker": [{"claims": claims}]})
    out = FactChecker(PROMPTS).run("Check.", w.context({"writer": draft}), llm)
    prompt = llm.prompts("fact_checker")[0]
    assert "RAG text." in prompt and "Fine-tuning text." in prompt and "UNCITED" not in prompt
    assert "1. [cites 1] RAG retrieves documents at query time [E1]." in prompt
    assert "Short [E2]" not in prompt  # too short to be a claim
    assert out.result.content.startswith("Fact check: 1 supported, 1 unsupported, 0 contradicted")
    assert out.result.evidence_ns == (1, 2)

    nothing = FakeLLM()
    assert FactChecker(PROMPTS).run("Check.", w.context(), nothing).result.failed
    assert nothing.calls() == 0
    assert cited_sentences("No citations here at all.") == []


# W6
def test_step_counter_counts_and_checks_tool_arguments_for_the_canary():
    class Echo:
        def __init__(self, response):
            self.response = response

        def generate(self, request):
            return self.response

    cached = LLMResponse(None, [], {"x": 1}, USAGE, "stop", cached=True)
    counter = StepCounter(Echo(cached), OutputValidator(CANARY))
    counter.generate(LLMRequest("s", []))
    meta = counter.meta("m")
    assert meta.cached and meta.cached_calls == 1 and meta.real_calls == 0

    leak = LLMResponse(None, [ToolCall("c", "search_web", {"query": CANARY})], None, USAGE, "stop")
    with pytest.raises(CanaryLeak):
        StepCounter(Echo(leak), OutputValidator(CANARY)).generate(LLMRequest("s", []))


def test_truncated_writer_answer_is_noticed():
    class Cut(FakeLLM):
        def generate(self, request):
            r = super().generate(request)
            return LLMResponse(r.text, [], None, r.usage, "max_tokens")

    notes = WorkerResult("researcher", "- note [E1]")
    out = Writer(PROMPTS).run("Write.", World().context({"researcher": notes}), Cut())
    assert ("The writer's answer was cut off at the output limit.", "truncated") in out.notices
