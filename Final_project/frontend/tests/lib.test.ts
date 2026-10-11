import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AGENT_ORDER,
  agentKey,
  agentTitle,
  boardFromRun,
  emptyBoard,
  eventsFromRun,
  reduce,
  resultNote,
  working,
} from "@/lib/agents";
import { citedIds, linkCitations, safeUrl } from "@/lib/citations";
import { parseEvent, subscribe } from "@/lib/events";
import {
  domain,
  flagLabel,
  formatBytes,
  formatCost,
  formatDate,
  formatDuration,
  formatMs,
  plural,
  shorten,
} from "@/lib/format";
import { replayFrames } from "@/lib/replay";
import { orderedSources, reportToMarkdown, sourceLocation } from "@/lib/report";
import {
  DocumentListSchema,
  EntrySchema,
  HealthSchema,
  ResearchViewSchema,
  type ResultEntry,
  type RunEvent,
} from "@/lib/schemas";
import { addToThread, isRunId, loadThread, MAX_THREAD, removeFromThread, saveThread, THREAD_KEY } from "@/lib/thread";
import { evidenceRange, rewriteMarkers, toolInfo, toolInput } from "@/lib/tools";
import { getWorkspaceId, isWorkspaceId, newWorkspaceId, resetWorkspaceMemory, WORKSPACE_KEY } from "@/lib/workspace";
import {
  approvalRun,
  completedRun,
  documentsRun,
  liveEvents,
  notice,
  partial,
  raw,
  runView,
  toolCall,
  toolStory,
} from "./fixtures";

const ev = (e: Omit<RunEvent, "id">, id = 1) => ({ ...e, id }) as RunEvent;
const history = completedRun.conversation_history;
const result = (agent: string) => history.find((e) => e.action === "RESULT" && e.agent === agent) as ResultEntry;

describe("schemas parse every real fixture", () => {
  it("views, health, documents and the SSE stream", () => {
    for (const v of [raw.completed, raw.documentsRun, raw.approval]) expect(ResearchViewSchema.safeParse(v).success).toBe(true);
    expect(HealthSchema.safeParse(raw.health).success).toBe(true);
    expect(DocumentListSchema.safeParse(raw.documents).success).toBe(true);
    expect(DocumentListSchema.parse(raw.documentsUpload).workspace?.limits?.max_documents).toBe(10);
    expect(liveEvents.map((e) => e.type)).toContain("tool_call");
    expect(completedRun.report?.sources.length).toBeGreaterThan(0);
    expect(approvalRun.status).toBe("awaiting_approval");
  });

  it("keeps unknown history actions as generic entries, rejects malformed known ones", () => {
    const base = { seq: 1, iteration: 1, agent: "System" };
    expect(EntrySchema.parse({ ...base, action: "HANDOFF", text: "x" }).action).toBe("HANDOFF");
    expect(EntrySchema.safeParse({ ...base, action: "TOOL_CALL" }).success).toBe(false);
    const parsed = ResearchViewSchema.parse({
      ...raw.completed,
      conversation_history: [...raw.completed.conversation_history, { ...base, seq: 99, action: "NEW_THING" }],
    });
    expect(parsed.conversation_history.at(-1)?.action).toBe("NEW_THING");
  });
});

describe("agent board reducer", () => {
  it("follows the real SSE stream: deciding, tool call, evidence, done, report", () => {
    let b = emptyBoard();
    const seen: string[][] = [];
    for (const e of liveEvents) {
      b = reduce(b, e, 1000);
      seen.push(working(b));
    }
    expect(seen).toContainEqual(["researcher"]);
    expect(b.status).toBe("completed");
    expect(b.evidence).toBe(3);
    expect(b.reportReady).toEqual({ title: completedRun.report!.title, sections: 4 });
    expect(b.cards.researcher).toMatchObject({ state: "done", note: "1 tool call · 3 evidence cited" });
    expect(b.cards.reviewer.note).toBe("approve · 1 issue");
    expect(b.cards.writer.note).toBe("draft ready");
    expect(b.cards.fact_checker.state).toBe("idle");
    const kinds = b.feed.map((f) => (f.kind === "entry" ? f.entry.action : "text"));
    expect(kinds).toContain("TOOL_CALL");
    expect(b.feed.at(-1)).toMatchObject({ kind: "text", text: "Report ready" });
  });

  it("dedups entries replayed after a reconnect (same seq)", () => {
    let b = emptyBoard();
    for (const e of liveEvents) b = reduce(b, e, 0);
    const n = b.feed.length;
    for (const e of liveEvents.filter((x) => ["decision", "tool_call", "worker_result"].includes(x.type))) b = reduce(b, e, 0);
    expect(b.feed.length).toBe(n);
    b = reduce(b, ev({ type: "notice", data: notice() as never }), 0);
    b = reduce(b, ev({ type: "notice", data: notice() as never }), 0);
    expect(b.feed.length).toBe(n + 1);
  });

  it("rebuilds a finished run from its history, same as the live stream", () => {
    const stored = boardFromRun(completedRun);
    let live = emptyBoard();
    for (const e of liveEvents) live = reduce(live, e, 0);
    const actions = (bd: typeof stored) => bd.feed.flatMap((f) => (f.kind === "entry" ? [f.entry.seq] : []));
    expect(actions(stored)).toEqual(actions(live));
    expect(stored.evidence).toBe(3);
    expect(stored.iteration).toBe(4);
    const docs = boardFromRun(documentsRun);
    expect(docs.evidence).toBe(6);
  });

  it("parallel delegations start together", () => {
    const d = history[0] as Extract<(typeof history)[number], { action: "DELEGATE" }>;
    const parallel = runView({
      conversation_history: [
        { ...d, delegations: [...d.delegations, { target_agent: "Fact-Checker", task: "Check" }] },
        ...history.slice(1),
      ],
    });
    const events = eventsFromRun(parallel);
    const i = events.findIndex((e) => e.type === "decision");
    expect(events.slice(i + 1, i + 3).map((e) => e.type)).toEqual(["agent_started", "agent_started"]);
    const frames = replayFrames(parallel);
    expect(frames[i + 2].delayMs).toBe(0);
    expect(replayFrames(parallel, 0.5)[1].delayMs).toBeLessThan(frames[1].delayMs);
  });

  it("approval, feedback, failure and cancellation", () => {
    let b = boardFromRun(approvalRun);
    expect(b.status).toBe("awaiting_approval");
    expect(working(b)).toEqual([]);
    const feedback = {
      seq: 9,
      iteration: 4,
      agent: "Human",
      model: null,
      llm_calls: 0,
      cached: false,
      duration_ms: 0,
      action: "FEEDBACK" as const,
      text: "Add an example",
    };
    b = reduce(b, ev({ type: "feedback", data: feedback }), 0);
    expect(b.status).toBe("queued");
    b = reduce(b, ev({ type: "run_started", data: { iteration: 4, iteration_limit: 6 } }), 0);
    expect(b.feed.at(-1)).toMatchObject({ text: "Revising after your feedback (2 more decisions)" });
    b = reduce(b, ev({ type: "agent_started", data: { agent: "writer", iteration: 5, task: "Revise" } }), 0);
    const failed = reduce(b, ev({ type: "failed", data: { error: "boom" } }), 0);
    expect(failed.cards.writer.state).toBe("failed");
    expect(failed.feed.at(-1)).toMatchObject({ text: "Run failed: boom" });
    const cancelled = reduce(b, ev({ type: "cancelled", data: { reason: null } }), 0);
    expect(cancelled.cards.writer.state).toBe("idle");
    expect(cancelled.feed.at(-1)).toMatchObject({ text: "Run cancelled" });
    const forced = reduce(b, ev({ type: "completed", data: { final_output: "x", forced: true, degraded: false } }), 0);
    expect(forced.feed.at(-1)).toMatchObject({ text: "Report ready (forced completion)" });
    const degraded = reduce(b, ev({ type: "completed", data: { final_output: "x", forced: false, degraded: true } }), 0);
    expect(degraded.feed.at(-1)).toMatchObject({ text: "Report ready (assembled automatically)" });
  });

  it("stored failed / cancelled runs and the events they imply", () => {
    expect(boardFromRun(partial(3, { status: "failed", error: "quota" })).status).toBe("failed");
    expect(eventsFromRun(partial(3, { status: "cancelled", error: null })).at(-1)?.type).toBe("cancelled");
    expect(eventsFromRun(partial(3, { status: "failed", error: null })).at(-1)).toMatchObject({ data: { error: "unknown error" } });
    const withNotice = partial(1, { conversation_history: [...history.slice(0, 1), notice({ seq: 2 })] });
    expect(eventsFromRun(withNotice).some((e) => e.type === "notice")).toBe(true);
  });

  it("result notes per agent", () => {
    expect(resultNote(result("Researcher"))).toBe("1 tool call · 3 evidence cited");
    expect(resultNote({ ...result("Researcher"), unsourced: true })).toBe("notes without sources");
    expect(resultNote({ ...result("Writer"), failed: true })).toBe("could not finish");
    const check = { ...result("Reviewer"), agent: "Fact-Checker", data: { claims: [{ status: "supported" }, { status: "unsupported" }] } };
    expect(resultNote(check)).toBe("1/2 claims supported");
    expect(resultNote({ ...result("Reviewer"), agent: "Other" })).toBe("done");
  });

  it("agent names: display names and SSE keys", () => {
    expect(agentKey("Fact-Checker")).toBe("fact_checker");
    expect(agentTitle("fact_checker")).toBe("Fact-Checker");
    expect(agentTitle("human")).toBe("You");
    expect(agentTitle("new_agent")).toBe("New agent");
    expect(AGENT_ORDER).toHaveLength(5);
  });
});

describe("tool calls", () => {
  it("labels, inputs and evidence ranges", () => {
    expect(toolInfo("search_web").label).toBe("Web search");
    expect(toolInfo("mystery_tool").label).toBe("mystery tool");
    expect(toolInput(toolCall({ args: { query: "rag" } }))).toBe("rag");
    expect(toolInput(toolCall({ args: { evidence_id: "E3" } }))).toBe("E3");
    expect(toolInput(toolCall({ args: { other: "x" } }))).toBe("x");
    expect(toolInput(toolCall({ args: {} }))).toBe("");
    expect(evidenceRange(["E1", "E2", "E3", "E5", "E7", "E8"])).toBe("E1–E3, E5, E7–E8");
    expect(evidenceRange(["E2", "E1", "E2"])).toBe("E1–E2");
    expect(evidenceRange(["X"])).toBe("X");
    expect(evidenceRange([])).toBe("");
  });

  it("the rewrite marker follows an empty or weak search with a different query or tool", () => {
    // weak web → wikipedia (different tool): marked; empty wikipedia → web (different): marked
    expect([...rewriteMarkers(toolStory)]).toEqual([3, 4]);
    // the same query again is not a rewrite; a capped call is ignored
    const same = [toolCall({ seq: 1, weak: true }), toolCall({ seq: 2, tool: "read_result", status: "capped" }), toolCall({ seq: 3 })];
    expect(rewriteMarkers(same).size).toBe(0);
    // a weak call in another task (iteration) does not mark this one
    const otherTask = [toolCall({ seq: 1, weak: true, iteration: 1 }), toolCall({ seq: 2, iteration: 2, args: { query: "x" } })];
    expect(rewriteMarkers(otherTask).size).toBe(0);
    // read_result after a weak search is not a search: no marker
    expect(rewriteMarkers([toolCall({ seq: 1, weak: true }), toolCall({ seq: 2, tool: "read_result", args: { evidence_id: "E1" } })]).size).toBe(0);
  });
});

describe("citations, report and safe links", () => {
  const known = new Set(["E1", "E3"]);
  it("links known citations outside code only; splits groups", () => {
    expect(linkCitations("A [E1]. B [E1, E3]. C [E9].", known)).toBe("A [E1](#cite-E1). B [E1](#cite-E1)[E3](#cite-E3). C [E9].");
    expect(linkCitations("`[E1]` and [E1][E3]", known)).toBe("`[E1]` and [E1](#cite-E1)[E3](#cite-E3)");
    expect(linkCitations("```\n[E1]\n```\n[E1](https://x)", known)).toBe("```\n[E1]\n```\n[E1](https://x)");
    expect(citedIds("x [E3] y [E1, E2] [E3]")).toEqual(["E3", "E1", "E2"]);
  });

  it("keeps only http(s) and anchors", () => {
    expect(safeUrl("https://a.b/c")).toBe("https://a.b/c");
    expect(safeUrl("#x")).toBe("#x");
    expect(safeUrl("javascript:alert(1)")).toBe("");
    expect(safeUrl("data:text/html,x")).toBe("");
    expect(safeUrl("not a url")).toBe("");
  });

  it("orders references by first citation; locations; Markdown export", () => {
    const report = completedRun.report!;
    const reordered = { ...report, summary: "Lead [E3].", sources: [...report.sources, { ...report.sources[0], id: "E9" }] };
    expect(orderedSources(reordered).map((s) => s.id)).toEqual(["E3", "E1", "E2", "E9"]);
    const docSource = documentsRun.report!.sources[0];
    expect(sourceLocation(docSource)).toBe("standard-webhooks-spec.md");
    expect(sourceLocation({ ...docSource, page: 4 })).toBe("standard-webhooks-spec.md, p. 4");
    expect(sourceLocation(report.sources[0])).toBe("example.org");
    expect(sourceLocation({ ...report.sources[0], url: null })).toBe("");
    const md = reportToMarkdown(report);
    expect(md.startsWith(`# ${report.title}\n`)).toBe(true);
    expect(md).toContain("## Overview");
    expect(md).toContain(`- ${report.references[0]}`);
    expect(reportToMarkdown({ ...report, references: [] })).not.toContain("## References");
  });
});

describe("thread storage (v2)", () => {
  const item = (n: number) => ({ run_id: `run_${n.toString(16).padStart(4, "0")}`, question: `q${n}`, created_at: "t" });
  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("adds, moves to the end, caps and removes", () => {
    let items = [item(1), item(2)];
    items = addToThread(items, item(1));
    expect(items.map((i) => i.question)).toEqual(["q2", "q1"]);
    for (let n = 3; n < 30; n++) items = addToThread(items, item(n));
    expect(items).toHaveLength(MAX_THREAD);
    expect(removeFromThread(items, items[0].run_id)).toHaveLength(MAX_THREAD - 1);
    expect(isRunId("run_abc")).toBe(true);
    expect(isRunId("run_XYZ")).toBe(false);
  });

  it("round-trips, ignores v1 entries and bad items", () => {
    saveThread([item(1)]);
    expect(loadThread()).toEqual([item(1)]);
    window.localStorage.setItem(THREAD_KEY, JSON.stringify([{ run_id: "run_ab", task: "v1 shape" }, item(2)]));
    expect(loadThread()).toEqual([item(2)]);
    window.localStorage.setItem(THREAD_KEY, "{broken");
    expect(loadThread()).toEqual([]);
    window.localStorage.setItem(THREAD_KEY, JSON.stringify({ not: "a list" }));
    expect(loadThread()).toEqual([]);
  });

  it("survives blocked storage", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(loadThread()).toEqual([]);
    expect(() => saveThread([item(1)])).not.toThrow();
  });
});

describe("workspace id", () => {
  afterEach(() => {
    window.localStorage.clear();
    resetWorkspaceMemory();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("is a lowercase UUIDv4, generated once and kept", () => {
    const id = getWorkspaceId();
    expect(isWorkspaceId(id)).toBe(true);
    expect(getWorkspaceId()).toBe(id);
    expect(window.localStorage.getItem(WORKSPACE_KEY)).toBe(id);
    window.localStorage.setItem(WORKSPACE_KEY, "not-a-uuid");
    expect(getWorkspaceId()).toBe(id); // the in-memory id replaces a bad saved one
  });

  it("works without randomUUID and with blocked storage", () => {
    vi.stubGlobal("crypto", { getRandomValues: (b: Uint8Array) => b.fill(7) });
    expect(isWorkspaceId(newWorkspaceId())).toBe(true);
    vi.unstubAllGlobals();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const a = getWorkspaceId();
    expect(isWorkspaceId(a)).toBe(true);
    expect(getWorkspaceId()).toBe(a);
  });
});

describe("format", () => {
  it("formats durations, costs, sizes, dates, domains and text", () => {
    expect(formatMs(450)).toBe("450 ms");
    expect(formatMs(1500)).toBe("1.5 s");
    expect(formatMs(125_000)).toBe("2 min 5 s");
    expect(formatDuration(45)).toBe("45 s");
    expect(formatDuration(125)).toBe("2 min 5 s");
    expect(formatDuration(7200)).toBe("about 2 h");
    expect(formatCost(0)).toBe("$0");
    expect(formatCost(0.00001)).toBe("< $0.0001");
    expect(formatCost(0.001518)).toBe("$0.0015");
    expect(formatBytes(500)).toBe("500 B");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatDate("2026-10-11T12:00:00Z")).toMatch(/Oct 11/);
    expect(formatDate("nope")).toBe("");
    expect(formatDate(null)).toBe("");
    expect(domain("https://en.wikipedia.org/wiki/X")).toBe("en.wikipedia.org");
    expect(domain("nope")).toBe("");
    expect(shorten("a  b   c", 10)).toBe("a b c");
    expect(shorten("abcdefghijkl", 6)).toBe("abcde…");
    expect(plural(1, "call")).toBe("1 call");
    expect(plural(2, "call")).toBe("2 calls");
    expect(flagLabel("cap_reached")).toBe("tool-call cap reached");
    expect(flagLabel("new_flag")).toBe("new flag");
  });
});

describe("events", () => {
  class FakeES {
    static CLOSED = 2;
    static last: FakeES;
    readyState = 1;
    closed = false;
    onerror: (() => void) | null = null;
    listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
    constructor(public url: string) {
      FakeES.last = this;
    }
    addEventListener(type: string, fn: (e: MessageEvent) => void) {
      (this.listeners[type] ??= []).push(fn);
    }
    close() {
      this.closed = true;
      this.readyState = 2;
    }
    emit(type: string, data: unknown, id = 1) {
      const rawEvent = { data: typeof data === "string" ? data : JSON.stringify(data), lastEventId: String(id) } as MessageEvent;
      this.listeners[type]?.forEach((fn) => fn(rawEvent));
    }
  }

  it("parses the real stream, closes on a terminal event, reports invalid ones", () => {
    const seen: string[] = [];
    let invalid = 0;
    subscribe("u", { onEvent: (e) => seen.push(e.type), onLost: () => {}, onInvalid: () => invalid++ }, FakeES as never);
    const es = FakeES.last;
    for (const e of raw.events) es.emit(e.type, e.data, e.id);
    es.emit("tool_call", { nope: true });
    es.emit("worker_result", "{broken");
    expect(seen).toEqual(raw.events.map((e) => e.type));
    expect(invalid).toBe(2);
    expect(es.closed).toBe(true);
    expect(parseEvent("failed", { data: JSON.stringify({ error: "x" }), lastEventId: "9" } as MessageEvent)).toEqual({
      id: 9,
      type: "failed",
      data: { error: "x" },
    });
  });

  it("reports a lost stream (CLOSED), not a reconnect (CONNECTING)", () => {
    let lost = 0;
    subscribe("u", { onEvent: () => {}, onLost: () => lost++, onInvalid: () => {} }, FakeES as never);
    const es = FakeES.last;
    es.readyState = 0;
    es.onerror?.();
    expect(lost).toBe(0);
    es.readyState = 2;
    es.onerror?.();
    es.onerror?.();
    expect(lost).toBe(1);
    let noImpl = 0;
    subscribe("u", { onEvent: () => {}, onLost: () => noImpl++, onInvalid: () => {} }, undefined).close();
    expect(noImpl).toBe(1);
  });
});
