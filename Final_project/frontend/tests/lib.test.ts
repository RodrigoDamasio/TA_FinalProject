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
import {
  addTurn,
  CHATS_KEY,
  chatFromView,
  chatOfRun,
  currentChat,
  EMPTY,
  importLegacy,
  isConversationId,
  isRunId,
  LEGACY_THREAD_KEY,
  legacyConversationId,
  loadChats,
  markReadOnly,
  MAX_CHATS,
  mergeConversation,
  removeChat,
  saveChats,
  setCurrent,
  upsertChat,
} from "@/lib/chats";
import { citedIds, linkCitations, plainPreview, safeUrl } from "@/lib/citations";
import { COLLAPSE_KEY, isExpanded, loadOverrides, resetRuns, saveOverrides, setAll } from "@/lib/collapse";
import { parseEvent, subscribe } from "@/lib/events";
import {
  domain,
  flagLabel,
  formatBytes,
  formatCost,
  formatDate,
  formatDuration,
  formatMs,
  formatRelative,
  plural,
  shorten,
} from "@/lib/format";
import { replayFrames } from "@/lib/replay";
import { orderedSources, reportToMarkdown, sourceLocation } from "@/lib/report";
import {
  AcceptedSchema,
  ConversationSchema,
  DocumentListSchema,
  EntrySchema,
  HealthSchema,
  ResearchViewSchema,
  type ResultEntry,
  type RunEvent,
  ToolSpecSchema,
} from "@/lib/schemas";
import { evidenceRange, rewriteMarkers, toolInfo, toolInput } from "@/lib/tools";
import { getWorkspaceId, isWorkspaceId, newWorkspaceId, resetWorkspaceMemory, WORKSPACE_KEY } from "@/lib/workspace";
import {
  acceptedFollowup,
  approvalRun,
  CONV,
  completedRun,
  conversation,
  conversationRunning,
  followupRun,
  followupWithContext,
  documentsRun,
  liveEvents,
  notice,
  partial,
  raw,
  runView,
  toolCall,
  toolStory,
  turn1Run,
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

describe("v1.2 conversations: schemas", () => {
  it("parse the recorded 2-turn chat, its 202s and GET /conversations", () => {
    expect(turn1Run).toMatchObject({ conversation_id: CONV, turn: 1 });
    expect(turn1Run.metadata.context_turns).toBe(0);
    expect(followupRun).toMatchObject({ conversation_id: CONV, turn: 2, question: "and cost?" });
    expect(acceptedFollowup).toMatchObject({ conversation_id: CONV, turn: 2 });
    expect(conversation.max_turns).toBe(30);
    expect(conversation.turns.map((t) => [t.turn, t.status])).toEqual([
      [1, "completed"],
      [2, "completed"],
    ]);
    expect(conversationRunning.turns[1]).toMatchObject({ status: "running", title: null });
    expect(ConversationSchema.safeParse({ conversation_id: CONV, turns: [] }).success).toBe(false);
  });

  it("older views and 202s (no conversation fields) still parse", () => {
    expect(completedRun.conversation_id).toBeUndefined();
    expect(completedRun.metadata.context_turns).toBe(0);
    expect(AcceptedSchema.parse(raw.accepted).turn).toBeUndefined();
  });

  it("context_turns and a search_conversation call (phase C) parse", () => {
    const v = ResearchViewSchema.parse(followupWithContext());
    expect(v.metadata.context_turns).toBe(1);
    const tools = v.conversation_history.filter((e) => e.action === "TOOL_CALL").map((e) => (e as { tool: string }).tool);
    expect(tools).toEqual(["search_conversation", "search_web"]);
    expect(v.conversation_history.map((e) => e.seq)).toEqual(v.conversation_history.map((_, i) => i + 1));
  });

  it("tool families: conversation and unknown future ones are accepted", () => {
    const spec = { name: "search_conversation", family: "conversation", description: "Search earlier turns." };
    expect(ToolSpecSchema.parse(spec).family).toBe("conversation");
    expect(ToolSpecSchema.parse({ ...spec, name: "search_maps", family: "maps", parameters: {} }).family).toBe("maps");
    expect(ToolSpecSchema.safeParse({ name: "x", description: "y" }).success).toBe(false);
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
    expect(toolInfo("search_conversation")).toEqual({ icon: "💬", label: "earlier turns" });
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
    expect(plainPreview("RAG is **cheap** [E1]. It [E2, E3] works  well [E4].")).toBe("RAG is cheap. It works well.");
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

describe("chat storage (v3)", () => {
  const T = (n: number, at = `2026-10-11T10:0${n}:00Z`) => ({
    run_id: `run_${n.toString(16).padStart(4, "0")}`,
    turn: n,
    question: `q${n}`,
    created_at: at,
  });
  const cid = (n: number) => `conv_${n.toString(16).padStart(16, "0")}`;
  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("ids: runs, conversations and the backend's legacy rule", () => {
    expect(isRunId("run_abc")).toBe(true);
    expect(isRunId("run_XYZ")).toBe(false);
    expect(isConversationId(CONV)).toBe(true);
    expect(isConversationId("conv_XYZ")).toBe(false);
    expect(legacyConversationId("run_abcdef012345")).toBe("conv_abcdef0123450000"); // backend test value
  });

  it("adds turns (a new chat becomes current), follow-ups keep the title, newest chat first", () => {
    let s = addTurn(EMPTY, cid(1), T(1));
    expect(s.current).toBe(cid(1));
    s = addTurn(s, cid(1), { ...T(2), question: "and cost?" });
    expect(currentChat(s)?.title).toBe("q1");
    expect(currentChat(s)?.turns.map((t) => t.turn)).toEqual([1, 2]);
    s = addTurn(s, cid(2), T(3));
    expect(s.chats.map((c) => c.conversation_id)).toEqual([cid(2), cid(1)]);
    expect(chatOfRun(s, T(2).run_id)?.conversation_id).toBe(cid(1));
    expect(chatOfRun(s, "run_ffff")).toBeNull();
    s = setCurrent(s, cid(1));
    s = removeChat(s, cid(1));
    expect(s.current).toBeNull();
    expect(s.chats).toHaveLength(1);
    expect(removeChat(addTurn(s, cid(5), T(5)), cid(2)).current).toBe(cid(5));
  });

  it("keeps the newest MAX_CHATS, never the current one dropped", () => {
    let s = addTurn(EMPTY, cid(0), T(1, "2000-01-01T00:00:00Z"));
    for (let n = 1; n <= MAX_CHATS + 2; n++) {
      const other = addTurn(EMPTY, cid(n), T(1, `2026-10-11T10:${String(n).padStart(2, "0")}:00Z`)).chats[0];
      s = upsertChat(s, other); // the current chat (cid 0) is the oldest
    }
    expect(s.chats).toHaveLength(MAX_CHATS);
    expect(s.chats.some((c) => c.conversation_id === cid(0))).toBe(true);
  });

  it("merges the server's turns, keeping local sources and turns still in flight", () => {
    let s = addTurn(EMPTY, CONV, { ...T(1), run_id: conversation.turns[0].run_id, sources: ["web"] });
    s = addTurn(s, CONV, { ...T(3), run_id: "run_0003aa", turn: 3 });
    s = mergeConversation(s, conversation);
    const chat = currentChat(s)!;
    expect(chat.turns.map((t) => t.run_id)).toEqual([...conversation.turns.map((t) => t.run_id), "run_0003aa"]);
    expect(chat.turns[0].sources).toEqual(["web"]);
    expect(chat.turns[1].question).toBe("and cost?");
    // an unknown chat is created from the server's turns; an empty one changes nothing
    const fresh = mergeConversation(EMPTY, conversationRunning);
    expect(fresh.chats[0].title).toBe(conversation.turns[0].question);
    expect(fresh.current).toBeNull();
    expect(mergeConversation(EMPTY, { ...conversation, turns: [] })).toBe(EMPTY);
  });

  it("read-only chats: from a view without a conversation, or gone from the server", () => {
    const own = chatFromView({ ...completedRun, conversation_id: undefined, turn: undefined }, "2026-10-11T00:00:00Z");
    expect(own.conversation_id).toBe(legacyConversationId(completedRun.run_id));
    expect(own.read_only).toBe(true);
    expect(chatFromView(turn1Run, "x").conversation_id).toBe(CONV);
    const s = addTurn(EMPTY, cid(1), T(1));
    expect(currentChat(markReadOnly(s, cid(1)))?.read_only).toBe(true);
    expect(markReadOnly(s, cid(9))).toBe(s);
  });

  it("round-trips, drops bad chats and turns, forgets an unknown current chat", () => {
    const s = addTurn(EMPTY, cid(1), T(1));
    saveChats(s);
    expect(loadChats()).toEqual(s);
    window.localStorage.setItem(
      CHATS_KEY,
      JSON.stringify({
        current: cid(7),
        chats: [
          { ...s.chats[0], turns: [T(1), { run_id: "bad" }] },
          { conversation_id: "nope", title: "x", created_at: "t", updated_at: "t", turns: [] },
          "junk",
        ],
      }),
    );
    expect(loadChats()).toEqual({ current: null, chats: s.chats });
    window.localStorage.setItem(CHATS_KEY, "{broken");
    expect(loadChats()).toEqual(EMPTY);
    window.localStorage.setItem(CHATS_KEY, JSON.stringify({ not: "chats" }));
    expect(loadChats()).toEqual(EMPTY);
  });

  it("imports v2 items once: one read-only chat per run", () => {
    const v2 = [
      { run_id: "run_00aa", question: "Older", created_at: "2026-10-10T10:00:00Z", depth: "brief", sources: ["web"] },
      { run_id: "run_ab", task: "v1 shape" },
      { run_id: "run_00bb", question: "Newer", created_at: "2026-10-11T10:00:00Z" },
    ];
    window.localStorage.setItem(LEGACY_THREAD_KEY, JSON.stringify(v2));
    const s = loadChats();
    expect(s.current).toBeNull();
    expect(s.chats.map((c) => [c.title, c.read_only, c.turns.length])).toEqual([
      ["Newer", true, 1],
      ["Older", true, 1],
    ]);
    expect(s.chats[1].conversation_id).toBe(legacyConversationId("run_00aa"));
    expect(s.chats[1].turns[0]).toMatchObject({ depth: "brief", sources: ["web"], turn: 1 });
    expect(window.localStorage.getItem(LEGACY_THREAD_KEY)).toBeNull();
    expect(JSON.parse(window.localStorage.getItem(CHATS_KEY)!).chats).toHaveLength(2);
    expect(importLegacy("{broken")).toEqual([]);
    expect(importLegacy(JSON.stringify({ not: "a list" }))).toEqual([]);
    expect(importLegacy(null)).toEqual([]);
    expect(loadChats()).toEqual(s); // from v3 now
  });

  it("survives blocked storage", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(loadChats()).toEqual(EMPTY);
    expect(() => saveChats(EMPTY)).not.toThrow();
    expect(loadOverrides()).toEqual({});
    expect(() => saveOverrides({ a: true })).not.toThrow();
  });
});

describe("collapsed turns", () => {
  afterEach(() => window.sessionStorage.clear());

  it("only the latest is expanded by default; choices override; a new question resets", () => {
    expect(isExpanded({}, "run_1", true)).toBe(true);
    expect(isExpanded({}, "run_1", false)).toBe(false);
    let o = setAll({}, ["run_1", "run_2"], true);
    expect(isExpanded(o, "run_1", false)).toBe(true);
    o = setAll(o, ["run_2"], false);
    expect(isExpanded(o, "run_2", true)).toBe(false);
    o = resetRuns(o, ["run_1", "run_2"]);
    expect(o).toEqual({});
  });

  it("is kept in sessionStorage; bad data is ignored", () => {
    saveOverrides({ run_1: false });
    expect(loadOverrides()).toEqual({ run_1: false });
    window.sessionStorage.setItem(COLLAPSE_KEY, JSON.stringify({ run_1: "yes" }));
    expect(loadOverrides()).toEqual({});
    window.sessionStorage.setItem(COLLAPSE_KEY, "{broken");
    expect(loadOverrides()).toEqual({});
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

  it("relative times for the chat list", () => {
    const now = Date.parse("2026-10-11T12:00:00Z");
    expect(formatRelative("2026-10-11T11:59:30Z", now)).toBe("just now");
    expect(formatRelative("2026-10-11T11:55:00Z", now)).toBe("5 min ago");
    expect(formatRelative("2026-10-11T09:00:00Z", now)).toBe("3 h ago");
    expect(formatRelative("2026-10-09T12:00:00Z", now)).toBe("2 d ago");
    expect(formatRelative("2026-09-01T12:00:00Z", now)).toMatch(/Sep 1/);
    expect(formatRelative("nope", now)).toBe("");
    expect(formatRelative("2026-10-11T11:00:00Z", null)).toBe("");
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
