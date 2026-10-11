/** v1.2 conversations in the UI (plan Improving_Chat_UI_UX §8): chats, follow-ups, collapsible
 * turns, deep links and the conversation problems. Responses are the recorded 2-turn chat
 * (tests/fixtures/run-turn1.json, run-followup.json, conversation.json). */
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ChatList from "@/components/chat/ChatList";
import Turn from "@/components/chat/Turn";
import ResearchApp from "@/components/ResearchApp";
import { chatStore, collapseStore } from "@/hooks/useChats";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import { addTurn, CHATS_KEY, EMPTY, LEGACY_THREAD_KEY } from "@/lib/chats";
import type { EventHandlers } from "@/lib/events";
import { subscribe } from "@/lib/events";
import type { ResearchView } from "@/lib/schemas";
import { WORKSPACE_KEY } from "@/lib/workspace";
import {
  acceptedView,
  agentsList,
  CONV,
  completedRun,
  conversation,
  followupRun,
  followupWithContext,
  health,
  liveEvents,
  partial,
  raw,
  samplesList,
  turn1Run,
} from "./fixtures";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    health: vi.fn(),
    agents: vi.fn(),
    samples: vi.fn(),
    sample: vi.fn(),
    startResearch: vi.fn(),
    getResearch: vi.fn(),
    conversation: vi.fn(),
    approve: vi.fn(),
    reject: vi.fn(),
    deliveries: vi.fn(),
  };
});
vi.mock("@/lib/events", () => ({ subscribe: vi.fn() }));

const mocked = vi.mocked(api);
const WS = "2a97516c-1111-4222-8333-444455556666";
const RUN1 = turn1Run.run_id;
const RUN2 = followupRun.run_id;
const OTHER = "conv_00000000000000bb";
let streams: EventHandlers[] = [];
let runs: Record<string, ResearchView> = {};

function accept(run_id: string, conversation_id: string, turn: number) {
  mocked.startResearch.mockResolvedValueOnce({ ...acceptedView, run_id, conversation_id, turn });
}

/** Storage v3 with chats; the first is current unless `current` says otherwise. */
function seed(
  chats: { id: string; runs: ResearchView[]; readOnly?: boolean }[],
  current: string | null = chats[0]?.id ?? null,
) {
  const stored = chats.map((c, n) => ({
    conversation_id: c.id,
    title: c.runs[0].question,
    created_at: `2026-10-1${n}T10:00:00Z`,
    updated_at: `2026-10-1${n}T1${c.runs.length}:00:00Z`,
    turns: c.runs.map((r, i) => ({ run_id: r.run_id, turn: i + 1, question: r.question, created_at: `2026-10-1${n}T1${i}:00:00Z` })),
    ...(c.readOnly ? { read_only: true } : {}),
  }));
  window.localStorage.setItem(CHATS_KEY, JSON.stringify({ current, chats: stored }));
}

const toggleOf = (name: RegExp) => screen.getByRole("button", { name });
const chatsNav = () => screen.getByRole("navigation", { name: "Chats" });

async function type(text: string) {
  const user = userEvent.setup();
  const box = screen.getByLabelText("Research question");
  await user.clear(box);
  await user.type(box, text);
  return user;
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.localStorage.setItem(WORKSPACE_KEY, WS);
  chatStore.reset();
  collapseStore.reset();
  window.history.replaceState(null, "", "/");
  streams = [];
  runs = { [RUN1]: turn1Run, [RUN2]: followupRun };
  vi.mocked(subscribe).mockImplementation((_url, handlers) => {
    streams.push(handlers);
    return { close: () => {} };
  });
  mocked.health.mockResolvedValue(health);
  mocked.agents.mockResolvedValue(agentsList);
  mocked.samples.mockResolvedValue(samplesList);
  mocked.getResearch.mockImplementation(async (id: string) => {
    if (!runs[id]) throw new ApiError("not_found", "This run is no longer available. Runs are kept for 30 days.");
    return runs[id];
  });
  mocked.startResearch.mockReset();
  mocked.conversation.mockReset();
  mocked.conversation.mockRejectedValue(new ApiError("network", "Can't reach the research service."));
  mocked.deliveries.mockResolvedValue([]);
});

describe("follow-ups", () => {
  it("ask, then a follow-up in the same chat: turn 1 collapses, turn 2 is expanded and focused", async () => {
    accept(RUN1, CONV, 1);
    render(<ResearchApp />);
    const box = screen.getByLabelText("Research question");
    expect(box).toHaveAttribute("placeholder", expect.stringMatching(/^e\.g\. /));
    let user = await type(turn1Run.question);
    await user.click(screen.getByRole("button", { name: "Research" }));
    expect(mocked.startResearch.mock.calls[0][0]).not.toHaveProperty("conversation_id");
    expect(await screen.findByRole("article", { name: "Report" })).toBeInTheDocument();
    expect(window.location.search).toBe(`?chat=${CONV}`);
    expect(screen.getAllByRole("heading", { level: 2, name: turn1Run.question })).toHaveLength(2); // chat + report title

    // the composer now asks for a follow-up; 2 characters are enough
    expect(box).toHaveAttribute("placeholder", "Ask a follow-up…");
    user = await type("a");
    expect(screen.getByText(/Write at least 2 characters\./)).toBeInTheDocument();
    accept(RUN2, CONV, 2);
    user = await type("and cost?");
    await user.click(screen.getByRole("button", { name: "Research" }));
    expect(mocked.startResearch.mock.calls[1][0]).toMatchObject({ question: "and cost?", conversation_id: CONV });

    const second = await screen.findByRole("button", { name: /^Follow-up · turn 2: and cost\?/ });
    expect(second).toHaveAttribute("aria-expanded", "true");
    await waitFor(() => expect(second).toHaveFocus());
    expect(toggleOf(/^Turn 1:/)).toHaveAttribute("aria-expanded", "false");
    expect(await screen.findAllByRole("article", { name: "Report" })).toHaveLength(1); // turn 2's only
    const turn1 = document.getElementById(`turn-${RUN1}`)!;
    expect(within(turn1).getByText(turn1Run.report!.title, { selector: "p" })).toBeInTheDocument(); // collapsed card
    expect(within(turn1).getByText("1 tool call")).toBeInTheDocument();
    expect(within(turn1).getByText("Brief")).toBeInTheDocument();

    // "How the agents worked" is closed for a finished turn, with its one-line summary
    const turn2 = document.getElementById(`turn-${RUN2}`)!;
    const activity = within(turn2).getByText(/How the agents worked/).closest("details")!;
    expect(activity.open).toBe(false);
    expect(activity.querySelector("summary")).toHaveTextContent(/1 tool call · \d+ evidence · 4 decisions/);

    // manual toggle, then Collapse all / Expand all
    await user.click(toggleOf(/^Turn 1:/));
    expect(toggleOf(/^Turn 1:/)).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByRole("article", { name: "Report" })).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(screen.queryAllByRole("article", { name: "Report" })).toHaveLength(0);
    expect(JSON.parse(window.sessionStorage.getItem("research-collapse-v1")!)).toEqual({ [RUN1]: false, [RUN2]: false });
    await user.click(screen.getByRole("button", { name: "Expand all" }));
    expect(screen.getAllByRole("article", { name: "Report" })).toHaveLength(2);
  });

  it("the composer is disabled while a turn runs; the activity collapses when it completes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const RUN = completedRun.run_id;
    runs[RUN] = partial(0, { status: "queued" });
    accept(RUN, CONV, 1);
    render(<ResearchApp />);
    const user = await type(completedRun.question);
    await user.click(screen.getByRole("button", { name: "Research" }));
    await waitFor(() => expect(streams).toHaveLength(1));
    for (const e of liveEvents.slice(0, 4)) act(() => streams[0].onEvent(e));
    expect(screen.getByLabelText("Research question")).toHaveAttribute("placeholder", "Ask a follow-up…");
    expect(screen.getByText(/Wait for the current answer to finish\./)).toBeInTheDocument();
    const activity = () => screen.getByText(/How the agents worked/).closest("details")!;
    expect(activity().open).toBe(true);
    for (const e of liveEvents.slice(4)) act(() => streams[0].onEvent(e));
    runs[RUN] = completedRun;
    await act(() => vi.advanceTimersByTimeAsync(400));
    expect(await screen.findByRole("article", { name: "Report" })).toBeInTheDocument();
    expect(activity().open).toBe(false);
    expect(screen.queryByText(/Wait for the current answer/)).toBeNull();
    vi.useRealTimers();
  });

  it("conversation-busy, conversation-full and conversation-not-found on send", async () => {
    seed([{ id: CONV, runs: [turn1Run] }]);
    render(<ResearchApp />);
    expect(await screen.findByRole("article", { name: "Report" })).toBeInTheDocument();

    mocked.startResearch.mockRejectedValueOnce(new ApiError("conversation_busy", "This chat is still answering. Wait for the current answer."));
    let user = await type("and cost?");
    await user.click(screen.getByRole("button", { name: "Research" }));
    expect(await screen.findByText(/This chat is still answering/)).toBeInTheDocument();
    await waitFor(() => expect(mocked.getResearch.mock.calls.filter(([id]) => id === RUN1).length).toBeGreaterThanOrEqual(2));

    mocked.startResearch.mockRejectedValueOnce(new ApiError("conversation_full", "This chat has reached 30 questions. Start a new chat."));
    await user.click(screen.getByRole("button", { name: "Research" }));
    const alert = await screen.findByText(/This chat has reached 30 questions/);
    expect(within(alert.closest("[role=alert]") as HTMLElement).getByRole("button", { name: "Start a new chat" })).toBeInTheDocument();

    mocked.startResearch.mockRejectedValueOnce(new ApiError("conversation_not_found", "This chat is no longer available on the server. Start a new chat."));
    await user.click(screen.getByRole("button", { name: "Research" }));
    expect(await screen.findByText(/This chat is no longer available on the server/)).toBeInTheDocument();
    expect(screen.getByText(/your question starts a new chat/)).toBeInTheDocument(); // now read-only
    expect(screen.getByText(/Write at least 10 characters/)).toBeInTheDocument(); // a new chat's minimum

    // the next question starts a new chat
    accept(RUN2, OTHER, 1);
    user = await type("What does RAG cost in practice?");
    await user.click(screen.getByRole("button", { name: "Research" }));
    await waitFor(() => expect(mocked.startResearch).toHaveBeenCalledTimes(4));
    expect(mocked.startResearch.mock.calls[3][0]).not.toHaveProperty("conversation_id");
    expect(await within(chatsNav()).findByText("Chats (2)")).toBeInTheDocument();

    // "Start a new chat" from the banner
    mocked.startResearch.mockRejectedValueOnce(new ApiError("conversation_full", "This chat is full. Start a new chat."));
    user = await type("and cost?");
    await user.click(screen.getByRole("button", { name: "Research" }));
    await user.click(await screen.findByRole("button", { name: "Start a new chat" }));
    expect(screen.getByText("Ask a research question.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a chat at its turn limit, and limits sent by /health", async () => {
    mocked.health.mockResolvedValue({ ...health, limits: { ...health.limits, conversation_max_turns: 2, followup_min_chars: 3 } });
    seed([{ id: CONV, runs: [turn1Run] }]);
    const { unmount } = render(<ResearchApp />);
    expect(await screen.findByRole("article", { name: "Report" })).toBeInTheDocument();
    await type("ab");
    expect(await screen.findByText(/Write at least 3 characters\./)).toBeInTheDocument();
    unmount();
    chatStore.reset();
    seed([{ id: CONV, runs: [turn1Run, followupRun] }]);
    render(<ResearchApp />);
    expect(await screen.findByText(/This chat is full\. Start a new chat\./)).toBeInTheDocument();
  });
});

describe("chats", () => {
  it("New chat, the sidebar, switching chats and removing one", async () => {
    seed([{ id: CONV, runs: [turn1Run, followupRun] }]);
    render(<ResearchApp />);
    expect(await screen.findByText("Chats (1)")).toBeInTheDocument();
    const nav = chatsNav();
    expect(within(nav).getByRole("link", { name: /When should I use RAG/ })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByText(/^2 turns/)).toBeInTheDocument();
    const user = userEvent.setup();

    await user.click(within(nav).getByRole("button", { name: "New chat" }));
    expect(screen.getByText("Ask a research question.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Turns" })?.children).toHaveLength(0);
    expect(window.location.search).toBe("");
    expect(screen.getByLabelText("Research question")).toHaveFocus();
    expect(within(nav).queryByRole("link", { current: "page" })).toBeNull();

    const RUN = completedRun.run_id;
    runs[RUN] = completedRun;
    accept(RUN, OTHER, 1);
    await type(completedRun.question);
    await user.click(screen.getByRole("button", { name: "Research" }));
    expect(await within(nav).findByText("Chats (2)")).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: /What is retrieval augmented/ })).toHaveAttribute("aria-current", "page");

    // switch back: the link carries ?chat=, the click opens it in place
    const first = within(nav).getByRole("link", { name: /When should I use RAG/ });
    expect(first).toHaveAttribute("href", `/?chat=${CONV}`);
    await user.click(first);
    expect(window.location.search).toBe(`?chat=${CONV}`);
    expect(await screen.findByRole("button", { name: /^Follow-up · turn 2: and cost\?/ })).toBeInTheDocument();

    // remove (local only), with a confirmation
    await user.click(within(nav).getByRole("button", { name: /^Remove chat: When should I use RAG/ }));
    await user.click(within(screen.getByRole("group", { name: /^Confirm removing/ })).getByRole("button", { name: "Cancel" }));
    expect(within(nav).getByText("Chats (2)")).toBeInTheDocument();
    await user.click(within(nav).getByRole("button", { name: /^Remove chat: When should I use RAG/ }));
    await user.click(within(screen.getByRole("group", { name: /^Confirm removing/ })).getByRole("button", { name: "Remove" }));
    expect(within(nav).getByText("Chats (1)")).toBeInTheDocument();
    expect(screen.getByText("Ask a research question.")).toBeInTheDocument();
    expect(window.location.search).toBe("");
    // removing another chat keeps the current one
    await user.click(within(nav).getByRole("link", { name: /What is retrieval augmented/ }));
    expect(window.location.search).toBe(`?chat=${OTHER}`);
  });

  it("a known chat is refreshed once from the server (turns added elsewhere)", async () => {
    seed([{ id: CONV, runs: [turn1Run] }]);
    mocked.conversation.mockResolvedValue(conversation);
    render(<ResearchApp />);
    expect(await screen.findByRole("button", { name: /^Follow-up · turn 2:/ })).toHaveAttribute("aria-expanded", "true");
    expect(toggleOf(/^Turn 1:/)).toHaveAttribute("aria-expanded", "false");
    expect(mocked.conversation).toHaveBeenCalledTimes(1);
  });

  it("a known chat gone from the server becomes read-only", async () => {
    seed([{ id: CONV, runs: [turn1Run] }]);
    mocked.conversation.mockRejectedValue(new ApiError("conversation_not_found", "gone"));
    render(<ResearchApp />);
    expect(await screen.findByText(/your question starts a new chat/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull(); // the chat is still readable
  });

  it("v2 history is imported as read-only chats; a question there starts a new chat", async () => {
    window.localStorage.setItem(
      LEGACY_THREAD_KEY,
      JSON.stringify([{ run_id: RUN1, question: turn1Run.question, created_at: "2026-10-10T10:00:00Z" }]),
    );
    render(<ResearchApp />);
    expect(await screen.findByText("Chats (1)")).toBeInTheDocument();
    expect(screen.getByText(/read-only/)).toBeInTheDocument();
    expect(screen.getByText("Ask a research question.")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(within(chatsNav()).getByRole("link", { name: /When should I use RAG/ }));
    expect(await screen.findByRole("article", { name: "Report" })).toBeInTheDocument();
    expect(screen.getByText(/your question starts a new chat/)).toBeInTheDocument();
    expect(mocked.conversation).not.toHaveBeenCalled();
    accept(RUN2, OTHER, 1);
    await type("What does retrieval cost?");
    await user.click(screen.getByRole("button", { name: "Research" }));
    await waitFor(() => expect(mocked.startResearch).toHaveBeenCalled());
    expect(mocked.startResearch.mock.calls[0][0]).not.toHaveProperty("conversation_id");
    expect(await screen.findByText("Chats (2)")).toBeInTheDocument();
  });
});

describe("links", () => {
  it("?chat= of a chat this browser does not know rebuilds it from GET /conversations", async () => {
    let answer!: (c: typeof conversation) => void;
    mocked.conversation.mockReturnValue(new Promise((r) => (answer = r)));
    render(<ResearchApp initialChatId={CONV} />);
    expect(screen.getByText("Loading the chat…")).toBeInTheDocument();
    await act(async () => answer(conversation));
    expect(await screen.findByRole("button", { name: /^Follow-up · turn 2: and cost\?/ })).toHaveAttribute("aria-expanded", "true");
    expect(toggleOf(/^Turn 1:/)).toHaveAttribute("aria-expanded", "false");
    expect(mocked.conversation).toHaveBeenCalledWith(CONV);
    expect(JSON.parse(window.localStorage.getItem(CHATS_KEY)!).current).toBe(CONV);
    expect(await screen.findAllByRole("article", { name: "Report" })).toHaveLength(1);
  });

  it("?chat=&run= opens that turn expanded", async () => {
    mocked.conversation.mockResolvedValue(conversation);
    render(<ResearchApp initialChatId={CONV} initialRunId={RUN1} />);
    expect(await screen.findByRole("button", { name: /^Turn 1:/ })).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findAllByRole("article", { name: "Report" })).toHaveLength(2);
  });

  it("?run= alone (Telegram and callback links) opens the run's chat", async () => {
    mocked.conversation.mockResolvedValue(conversation);
    render(<ResearchApp initialRunId={RUN2} />);
    expect(await screen.findByRole("button", { name: /^Follow-up · turn 2:/ })).toBeInTheDocument();
    expect(mocked.conversation).toHaveBeenCalledWith(CONV);
    // a known run opens its chat without a request
  });

  it("a known run or chat opens from storage", async () => {
    seed([{ id: CONV, runs: [turn1Run, followupRun] }, { id: OTHER, runs: [completedRun] }], OTHER);
    runs[completedRun.run_id] = completedRun;
    const { unmount } = render(<ResearchApp initialRunId={RUN1} />);
    expect(await screen.findByRole("button", { name: /^Turn 1:/ })).toHaveAttribute("aria-expanded", "true");
    expect(mocked.getResearch).not.toHaveBeenCalledWith("run_ffffffffffff");
    unmount();
    chatStore.reset();
    render(<ResearchApp initialChatId={CONV} />);
    expect(await screen.findByRole("button", { name: /^Follow-up · turn 2:/ })).toBeInTheDocument();
  });

  it("a link to a chat gone from the server says so and offers a new chat", async () => {
    mocked.conversation.mockRejectedValue(
      api.toApiError({ status: 404, body: raw.problems["conversation-not-found"] }),
    );
    render(<ResearchApp initialChatId="conv_0000000000000000" />);
    expect(await screen.findByText(/This chat is no longer available on the server/)).toBeInTheDocument();
    expect(screen.getByText("Ask a research question.")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Start a new chat" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("invalid ids in the link are ignored", async () => {
    render(<ResearchApp initialChatId="conv_XYZ" initialRunId="nope" />);
    expect(await screen.findByText("Ask a research question.")).toBeInTheDocument();
    expect(mocked.conversation).not.toHaveBeenCalled();
    expect(mocked.getResearch).not.toHaveBeenCalled();
  });
});

describe("follow-up visibility (phase B + C, fixtures)", () => {
  const props = {
    id: `turn-${RUN2}`,
    question: "and cost?",
    turn: 2,
    missing: false,
    board: null,
    lost: false,
    sample: false,
    busy: false,
    feedbackMax: 1000,
    agents: agentsList,
    onApprove: () => {},
    onReject: () => {},
  };

  it("context from earlier turns and a search_conversation card", () => {
    render(<Turn {...props} view={followupWithContext()} expanded activityOpen onToggle={() => {}} />);
    expect(screen.getByRole("button", { name: /^Follow-up · turn 2: and cost\?/ })).toBeInTheDocument();
    expect(screen.getByText(/Used context from 1 earlier turn$/)).toBeInTheDocument();
    const card = screen.getByRole("group", { name: "Tool call: search_conversation, ok" });
    expect(card).toHaveTextContent("💬");
    expect(card).toHaveTextContent("earlier turns");
    expect(card).toHaveTextContent("via conversation");
    expect(card).toHaveTextContent("evidence E1–E2");
    expect(screen.getAllByTestId("tool-call")).toHaveLength(2);
    expect(screen.getByText(/2 tool calls · \d+ evidence · 4 decisions/)).toBeInTheDocument();
  });

  it("collapsed: status, badges and context; no Markdown; turn 1 has no follow-up label", () => {
    const failed = { ...followupWithContext(), status: "failed" as const, report: null, error: "quota" };
    const { rerender } = render(<Turn {...props} view={failed} expanded={false} activityOpen={false} onToggle={() => {}} />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("2 tool calls")).toBeInTheDocument();
    expect(screen.getByText(/Used context from 1 earlier turn/)).toBeInTheDocument();
    expect(screen.queryByRole("article")).toBeNull();
    const grounded = {
      ...turn1Run,
      metadata: { ...turn1Run.metadata, grounding: { checked: 4, supported: 3, unsupported: 1, contradicted: 0 } },
    };
    rerender(<Turn {...props} turn={1} question="" view={grounded} expanded={false} activityOpen={false} onToggle={() => {}} />);
    expect(screen.getByRole("button", { name: `Turn 1: ${turn1Run.question}` })).toBeInTheDocument();
    expect(screen.getByText("3/4 supported")).toBeInTheDocument();
    expect(screen.queryByText(/Used context/)).toBeNull();
    rerender(<Turn {...props} view={null} missing expanded={false} activityOpen={false} onToggle={() => {}} />);
    expect(screen.getByText(/This run is no longer available/)).toBeInTheDocument();
  });
});

describe("ChatList", () => {
  it("wide screens: a list with a heading; phones: a drawer", () => {
    const state = addTurn(EMPTY, CONV, { run_id: RUN1, turn: 1, question: "", created_at: "2026-10-11T10:00:00Z" });
    const matchMedia = vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() });
    vi.stubGlobal("matchMedia", matchMedia);
    const handlers = { onOpen: vi.fn(), onRemove: vi.fn(), onNew: vi.fn() };
    const { unmount } = render(<ChatList chats={state.chats} current={null} {...handlers} />);
    expect(screen.getByRole("heading", { name: "Chats (1)" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: new RegExp(CONV) })).toBeInTheDocument(); // no title: the id
    expect(matchMedia).toHaveBeenCalledWith("(min-width: 1024px)");
    unmount();
    vi.unstubAllGlobals();
    render(<ChatList chats={[]} current={null} {...handlers} />);
    expect(screen.getByText("Chats (0)").tagName).toBe("SUMMARY");
    expect(screen.getByText(/Your chats appear here/)).toBeInTheDocument();
  });
});
