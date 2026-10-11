import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ToolCallCard from "@/components/activity/ToolCallCard";
import ApprovalPanel from "@/components/ApprovalPanel";
import LibraryApp from "@/components/library/LibraryApp";
import Report from "@/components/report/Report";
import ResearchApp from "@/components/ResearchApp";
import RunDetails from "@/components/run/RunDetails";
import ServiceBanner from "@/components/ServiceBanner";
import Transcript from "@/components/transcript/Transcript";
import { threadStore } from "@/hooks/useThread";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import type { EventHandlers } from "@/lib/events";
import { subscribe } from "@/lib/events";
import type { Entry, ResearchView, RunEvent } from "@/lib/schemas";
import { THREAD_KEY } from "@/lib/thread";
import { WORKSPACE_KEY } from "@/lib/workspace";
import {
  acceptedView,
  agentsList,
  approvalRun,
  completedRun,
  delivery,
  doc,
  documentList,
  documentListUpload,
  documentsRun,
  health,
  liveEvents,
  notice,
  partial,
  RUN,
  runView,
  samplesList,
  toolCall,
  toolStory,
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
    approve: vi.fn(),
    reject: vi.fn(),
    deliveries: vi.fn(),
    listDocuments: vi.fn(),
    getDocument: vi.fn(),
    deleteDocument: vi.fn(),
    uploadDocument: vi.fn(),
  };
});
vi.mock("@/lib/events", () => ({ subscribe: vi.fn() }));

const mocked = vi.mocked(api);
const WS = "2a97516c-1111-4222-8333-444455556666";
let streams: EventHandlers[] = [];
let runs: Record<string, ResearchView> = {};

function emit(event: RunEvent) {
  act(() => streams[streams.length - 1].onEvent(event));
}

function accept(id = RUN) {
  mocked.startResearch.mockResolvedValue({ ...acceptedView, run_id: id });
}

async function ask(text = "What is retrieval augmented generation and when should I use it?") {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Research question"), text);
  await user.click(screen.getByRole("button", { name: "Research" }));
  return user;
}

function openDetails(summaryText: string | RegExp) {
  const summary = screen.getAllByText(summaryText)[0];
  const details = summary.closest("details")!;
  act(() => {
    details.open = true;
    details.dispatchEvent(new Event("toggle"));
  });
  return details;
}

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem(WORKSPACE_KEY, WS);
  threadStore.reset();
  window.history.replaceState(null, "", "/");
  streams = [];
  runs = { [RUN]: partial(0, { status: "queued" }) };
  vi.mocked(subscribe).mockImplementation((_url, handlers) => {
    streams.push(handlers);
    return { close: () => {} };
  });
  mocked.health.mockResolvedValue(health);
  mocked.agents.mockResolvedValue(agentsList);
  mocked.samples.mockResolvedValue(samplesList);
  mocked.getResearch.mockImplementation(async (id: string) => {
    if (!runs[id]) throw new ApiError("not_found", "This run is no longer available.");
    return runs[id];
  });
  mocked.startResearch.mockReset();
  mocked.approve.mockReset();
  mocked.reject.mockReset();
  mocked.deliveries.mockResolvedValue([delivery]);
});

describe("ResearchApp", () => {
  it("renders the composer, depth, sources, samples and the empty state", async () => {
    render(<ResearchApp initialRunId={null} />);
    expect(await screen.findByRole("button", { name: /RAG basics/ })).toBeVisible();
    expect(screen.getByText("Ask a research question.")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Detailed" })).toBeChecked();
    expect(screen.getByText("600–1000 words · 4–6 sections")).toBeInTheDocument();
    for (const s of ["Web", "Wikipedia", "Documents"]) expect(screen.getByRole("checkbox", { name: s })).toBeChecked();
    expect(screen.getByText("Advanced: decisions: by depth · approval off · memory on")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Research" })).toBeDisabled();
    expect(screen.getByText(/Write at least 10 characters/)).toBeInTheDocument();
    expect(await screen.findByText(/Demo mode:/)).toBeInTheDocument();
  });

  it("sends question, depth, sources, settings and the workspace; blocks a second send", async () => {
    accept();
    render(<ResearchApp initialRunId={null} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("radio", { name: "Brief" }));
    expect(screen.getByText("200–400 words · 2–3 sections")).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Web" }));
    await user.click(screen.getByRole("checkbox", { name: "Wikipedia" }));
    await user.click(screen.getByLabelText("Require my approval before the final report"));
    await user.click(screen.getByLabelText("Use memory of earlier reports"));
    fireEvent.change(screen.getByLabelText("Max decisions"), { target: { value: "3" } });
    await ask();
    expect(mocked.startResearch).toHaveBeenCalledWith(
      {
        question: "What is retrieval augmented generation and when should I use it?",
        depth: "brief",
        sources: ["documents"],
        max_iterations: 3,
        require_approval: true,
        use_memory: false,
      },
      WS,
    );
    expect(window.location.search).toBe(`?run=${RUN}`);
    expect(await screen.findByText(/Waiting in the queue/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Research" })).toBeDisabled();
    expect(screen.getByText(/Wait for the current run to finish/)).toBeInTheDocument();
    const saved = JSON.parse(window.localStorage.getItem(THREAD_KEY) ?? "[]");
    expect(saved[0]).toMatchObject({ run_id: RUN, depth: "brief", sources: ["documents"] });
  });

  it("needs at least one source; max decisions default is omitted", async () => {
    accept();
    render(<ResearchApp initialRunId={null} />);
    const user = userEvent.setup();
    for (const s of ["Web", "Wikipedia", "Documents"]) await user.click(screen.getByRole("checkbox", { name: s }));
    await user.type(screen.getByLabelText("Research question"), "A question long enough");
    expect(screen.getAllByText(/Choose at least one source/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Research" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "Wikipedia" }));
    await user.keyboard("{Enter}");
    await waitFor(() => expect(mocked.startResearch).toHaveBeenCalled());
    expect(mocked.startResearch.mock.calls[0][0]).not.toHaveProperty("max_iterations");
    expect(mocked.startResearch.mock.calls[0][0].sources).toEqual(["wikipedia"]);
  });

  it("live events drive the board, tool-call cards, evidence counter and status line", async () => {
    accept();
    render(<ResearchApp initialRunId={null} />);
    await ask();
    await waitFor(() => expect(streams).toHaveLength(1));
    const upTo = liveEvents.findIndex((e) => e.type === "worker_result");
    for (const e of liveEvents.slice(0, upTo)) emit(e);
    expect(screen.getByRole("listitem", { name: "Researcher: working" })).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "" })).toHaveTextContent("Researcher working.");
    expect(screen.getByText(/Researcher is working/)).toBeInTheDocument();
    const card = screen.getByRole("group", { name: "Tool call: search_web, ok" });
    expect(card).toHaveTextContent("what is retrieval augmented generation and when should i use it");
    expect(card).toHaveTextContent("via searxng");
    expect(card).toHaveTextContent("3 results");
    expect(card).toHaveTextContent("evidence E1–E3");
    expect(screen.getByTestId("evidence-counter")).toHaveTextContent("Evidence: 3");
    expect(screen.getByText("Supervisor → DELEGATE Researcher")).toBeInTheDocument();
    for (const e of liveEvents.slice(upTo)) emit(e);
    expect(screen.getByText(/researcher used search_web ×1/)).toBeInTheDocument();
    expect(screen.getByText("Supervisor → FINAL")).toBeInTheDocument();
  });

  it("refetches on completion and shows the report, chips, references and run details", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    accept();
    render(<ResearchApp initialRunId={null} />);
    await ask();
    await waitFor(() => expect(streams).toHaveLength(1));
    for (const e of liveEvents) emit(e);
    runs[RUN] = completedRun;
    await act(() => vi.advanceTimersByTimeAsync(400));
    const report = await screen.findByRole("article", { name: "Report" });
    expect(within(report).getByRole("heading", { level: 2 })).toHaveTextContent(completedRun.report!.title);
    expect(within(report).getByRole("heading", { name: "Overview" })).toBeInTheDocument();
    const refs = within(report).getByRole("region", { name: "References" });
    expect(within(refs).getAllByRole("listitem")).toHaveLength(3);
    expect(within(refs).getAllByRole("link")[0]).toHaveAttribute("rel", "noopener noreferrer nofollow");
    expect(screen.getByRole("status", { name: "" })).toHaveTextContent("Report ready.");
    expect(screen.getByText("667 words")).toBeInTheDocument();
    expect(screen.getByText(/Grounding: not fact-checked/)).toBeInTheDocument();
    openDetails("Run details");
    expect(screen.getByLabelText("Run metadata")).toBeDefined();
  });

  it("approval: approve, reject with feedback, conflict", async () => {
    runs = { [RUN]: approvalRun };
    window.localStorage.setItem(THREAD_KEY, JSON.stringify([{ run_id: RUN, question: approvalRun.question, created_at: "x" }]));
    render(<ResearchApp initialRunId={null} />);
    expect(await screen.findByRole("heading", { name: "Your approval is needed" })).toBeInTheDocument();
    const user = userEvent.setup();
    mocked.reject.mockResolvedValue(partial(8, { status: "queued" }));
    await user.type(screen.getByLabelText("Feedback for a revision (optional)"), "Add an example");
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(mocked.reject).toHaveBeenCalledWith(RUN, "Add an example");
    runs[RUN] = approvalRun;
    mocked.approve.mockRejectedValue(new ApiError("conflict", "The run already moved on."));
    const approveButton = await screen.findByRole("button", { name: "Approve" });
    await user.click(approveButton);
    expect(await screen.findByText("The run already moved on.")).toBeInTheDocument();
  });

  it("replays a sample without starting a run; 'Run it for real' starts one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mocked.sample.mockResolvedValue({ ...completedRun, sample: true });
    render(<ResearchApp initialRunId={null} />);
    fireEvent.click(await screen.findByRole("button", { name: /RAG basics/ }));
    expect(await screen.findByText("Sample run — replay (0 AI calls)")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(1500));
    expect(screen.getByRole("listitem", { name: "Supervisor: done" })).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(await screen.findByRole("article", { name: "Report" })).toBeInTheDocument();
    expect(screen.getAllByRole("group", { name: /Tool call: search_web/ }).length).toBeGreaterThan(0);
    expect(mocked.startResearch).not.toHaveBeenCalled();
    accept();
    fireEvent.click(screen.getByRole("button", { name: "Run it for real" }));
    await waitFor(() => expect(mocked.startResearch).toHaveBeenCalledWith(expect.objectContaining({ question: completedRun.question }), WS));
  });

  it("errors: field errors, 429 countdown, 503 rechecks health, a missing run", async () => {
    mocked.startResearch.mockRejectedValue(new ApiError("validation", "The request is not valid.", undefined, ["Question: too short"]));
    const { unmount } = render(<ResearchApp initialRunId={null} />);
    await ask();
    expect(await screen.findByText("Question: too short")).toBeInTheDocument();
    mocked.startResearch.mockRejectedValue(new ApiError("rate_limited", "You started too many runs in a short time.", 30));
    await userEvent.setup().click(screen.getByRole("button", { name: "Research" }));
    expect(await screen.findByText(/You can send again in (29|30) s/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Research" })).toBeDisabled();
    unmount();

    mocked.startResearch.mockRejectedValue(new ApiError("busy", "The research service is busy right now."));
    const second = render(<ResearchApp initialRunId={null} />);
    await ask();
    expect(await screen.findByText("The research service is busy right now.")).toBeInTheDocument();
    await waitFor(() => expect(mocked.health.mock.calls.length).toBeGreaterThanOrEqual(3));
    second.unmount();

    render(<ResearchApp initialRunId="run_dead" />);
    expect(await screen.findByText(/This run is no longer available/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Remove from the history" }));
    expect(screen.getByText("Ask a research question.")).toBeInTheDocument();
  });

  it("service level: minimal disables sending; search down and index warming notes", async () => {
    mocked.health.mockResolvedValue({
      ...health,
      service_level: "minimal",
      retry_after_s: 7200,
      search: { mode: "live", available: false },
      embedder: { mode: "onnx", status: "loading" },
    });
    render(<ResearchApp initialRunId={null} />);
    expect(await screen.findByText(/Today's free AI quota is used up/)).toBeInTheDocument();
    expect(screen.getByText(/New runs open again in about 2 h/)).toBeInTheDocument();
    expect(screen.getByText(/^Today's AI quota is used up\. 0\/2000$/)).toBeInTheDocument();
    expect(screen.getByText(/Web search is unavailable: the Researcher falls back/)).toBeInTheDocument();
    expect(screen.getByText(/Web search is unavailable right now: the Researcher falls back to Wikipedia/)).toBeInTheDocument();
    expect(screen.getAllByText(/The document index is warming up/)).toHaveLength(2);
  });

  it("a lost stream falls back to polling until the run ends", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    accept();
    render(<ResearchApp initialRunId={null} />);
    await ask();
    await waitFor(() => expect(streams).toHaveLength(1));
    act(() => streams[0].onLost());
    expect(await screen.findByText(/Live updates interrupted/)).toBeInTheDocument();
    runs[RUN] = completedRun;
    await act(() => vi.advanceTimersByTimeAsync(3500));
    expect(await screen.findByRole("article", { name: "Report" })).toBeInTheDocument();
  });

  it("an invalid event is reported as a contract error", async () => {
    accept();
    render(<ResearchApp initialRunId={null} />);
    await ask();
    await waitFor(() => expect(streams).toHaveLength(1));
    act(() => streams[0].onInvalid());
    expect(await screen.findByText("Unexpected update from the server.")).toBeInTheDocument();
  });

  it("restores the history from storage; only the last run is followed; remove one", async () => {
    const older = runView({ run_id: "run_01d", question: "Older question" });
    runs = { run_01d: older, [RUN]: partial(2) };
    window.localStorage.setItem(
      THREAD_KEY,
      JSON.stringify([
        { run_id: "run_01d", question: "Older question", created_at: "2026-10-11T10:00:00Z" },
        { run_id: RUN, question: completedRun.question, created_at: "2026-10-11T11:00:00Z" },
      ]),
    );
    render(<ResearchApp initialRunId={null} />);
    expect(await screen.findByText("History (2)")).toBeInTheDocument();
    expect(screen.getAllByText("Older question").length).toBeGreaterThan(0);
    await waitFor(() => expect(streams).toHaveLength(1));
    expect(await screen.findAllByRole("article", { name: "Report" })).toHaveLength(1);
    const nav = screen.getByRole("navigation", { name: "History" });
    expect(within(nav).getByRole("link", { name: /Older question/ })).toHaveAttribute("href", "#turn-run_01d");
    await userEvent.setup().click(within(nav).getByRole("button", { name: "Remove from history: Older question" }));
    expect(screen.getByText("History (1)")).toBeInTheDocument();
  });

  it("?run= joins the history", async () => {
    runs = { [RUN]: completedRun };
    render(<ResearchApp initialRunId={RUN} />);
    expect(await screen.findByRole("article", { name: "Report" })).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem(THREAD_KEY) ?? "[]")[0].run_id).toBe(RUN);
  });

  it("failed and cancelled runs, flags, memory and injection notes", async () => {
    runs = {
      [RUN]: runView({ status: "failed", report: null, error: "LLM quota exhausted", flags: ["possible_injection"] }),
    };
    window.localStorage.setItem(THREAD_KEY, JSON.stringify([{ run_id: RUN, question: "q", created_at: "x" }]));
    const { unmount } = render(<ResearchApp initialRunId={null} />);
    expect(await screen.findByText("The run failed: LLM quota exhausted")).toBeInTheDocument();
    expect(screen.getByText(/prompt-injection attempts were found/)).toBeInTheDocument();
    unmount();
    threadStore.reset();
    runs = {
      [RUN]: runView({ status: "cancelled", report: null, memory_used: [{ run_id: "run_0", question: "Earlier one" }] }),
    };
    render(<ResearchApp initialRunId={null} />);
    expect(await screen.findByText("This run was cancelled.")).toBeInTheDocument();
    expect(screen.getByText(/Used memory from 1 earlier report: Earlier one/)).toBeInTheDocument();
  });
});

describe("ToolCallCard variants", () => {
  const cases: [string, Parameters<typeof toolCall>[0], RegExp[]][] = [
    ["ok", {}, [/ok/, /3 results/]],
    ["empty", { status: "empty", results: 0, evidence: [] }, [/empty/, /0 results/]],
    ["weak", { weak: true }, [/weak/]],
    ["repeat", { repeat: true }, [/repeat/]],
    ["fallback", { fallback: "wikipedia" }, [/fallback → wikipedia/]],
    ["capped", { status: "capped", tool: "read_result", args: { evidence_id: "E2" } }, [/capped/, /tool-call cap reached/, /E2/]],
    ["refused", { status: "refused", error: "Only web evidence can be read." }, [/refused/, /Only web evidence can be read/]],
    ["error", { status: "error", error: "search unavailable" }, [/error/, /search unavailable/]],
  ];
  it.each(cases)("%s", (_name, overrides, texts) => {
    render(<ToolCallCard entry={toolCall({ duration_ms: 1500, ...overrides })} rewrote={false} />);
    const card = screen.getByTestId("tool-call");
    for (const t of texts) expect(card).toHaveTextContent(t);
    expect(card).not.toHaveTextContent("rewrote the query");
  });

  it("shows the rewrite marker in the transcript of a weak → rewritten task", () => {
    render(<Transcript run={runView({ conversation_history: [...toolStory, notice({ seq: 6 })] })} />);
    expect(screen.getAllByText("rewrote the query")).toHaveLength(2);
    expect(screen.getByText(/Note \(tool-call cap reached\):/)).toBeInTheDocument();
  });
});

describe("Report", () => {
  it("chips show a popover on focus and link to the reference", async () => {
    render(<Report run={documentsRun} />);
    const chip = screen.getAllByRole("link", { name: /^E1: Standard Webhooks specification/ })[0];
    expect(chip).toHaveAttribute("href", `#${documentsRun.run_id}-ref-E1`);
    act(() => chip.focus());
    const tip = await screen.findByRole("tooltip");
    expect(tip).toHaveTextContent("Documents · standard-webhooks-spec.md");
    expect(chip).toHaveAttribute("aria-describedby", tip.id);
    fireEvent.keyDown(chip, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.mouseEnter(chip.parentElement!);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    act(() => {
      window.dispatchEvent(new Event("scroll"));
    });
    expect(screen.getByRole("tooltip")).toBeInTheDocument(); // follows the chip
    fireEvent.mouseLeave(chip.parentElement!);
    expect(screen.queryByRole("tooltip")).toBeNull();
    const refs = screen.getByRole("region", { name: "References" });
    expect(within(refs).queryAllByRole("link")).toHaveLength(0); // documents have no URL
    expect(refs).toHaveTextContent("standard-webhooks-spec.md");
  });

  it("forced / degraded banners, grounding, unknown citations stay plain; copy and download", async () => {
    const run = runView({
      metadata: { ...completedRun.metadata, forced: true, grounding: { checked: 4, supported: 3, unsupported: 1, contradicted: 0 } },
      report: { ...completedRun.report!, summary: "Lead [E1] and [E42]." },
    });
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const createObjectURL = vi.fn(() => "blob:x");
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    const { unmount } = render(<Report run={run} />);
    expect(screen.getByText(/ran out of decisions/)).toBeInTheDocument();
    expect(screen.getByText(/3 of 4 claims supported/)).toBeInTheDocument();
    expect(screen.getByText(/\[E42\]/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Copy as Markdown" }));
    expect(writeText.mock.calls[0][0]).toContain(`# ${completedRun.report!.title}`);
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Download .md" }));
    expect(createObjectURL).toHaveBeenCalled();
    unmount();
    render(<Report run={runView({ metadata: { ...completedRun.metadata, degraded: true } })} />);
    expect(screen.getByText(/assembled automatically/)).toBeInTheDocument();
  });

  it("renders untrusted Markdown inert: no HTML, no images, no javascript: links", () => {
    const evil = runView({
      report: {
        ...completedRun.report!,
        sections: [
          {
            heading: "<b>Bold</b>",
            content:
              'Text <script>alert(1)</script><img src=x onerror=alert(1)> ![pic](https://x/y.png) [bad](javascript:alert(1)) [ok](https://good.example/a) `[E1]`\n\n| a | b |\n|---|---|\n| 1 | 2 |',
          },
        ],
      },
    });
    const { container } = render(<Report run={evil} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("<b>Bold</b>")).toBeInTheDocument();
    expect(screen.getByText("[image: pic]")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "bad" })).toBeNull();
    expect(screen.getByRole("link", { name: /ok/ })).toHaveAttribute("href", "https://good.example/a");
    expect(screen.getByRole("region", { name: "Table" })).toBeInTheDocument();
    expect(container.querySelector("code")?.textContent).toBe("[E1]");
  });
});

describe("ApprovalPanel", () => {
  it("approve, reject with feedback, expiry countdown, no revisions left", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-10-11T13:00:00Z"));
    const onApprove = vi.fn();
    const onReject = vi.fn();
    const run = runView({ ...approvalRun, approval: { ...approvalRun.approval, expires_at: "2026-10-11T13:10:00Z" } });
    const { rerender } = render(<ApprovalPanel run={run} busy={false} feedbackMax={1000} onApprove={onApprove} onReject={onReject} />);
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(screen.getByText(/expires in (9|10) min/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onApprove).toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Feedback for a revision (optional)"), { target: { value: "  More  " } });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onReject).toHaveBeenCalledWith("More");
    rerender(
      <ApprovalPanel
        run={{ ...run, approval: { ...run.approval, revisions: 1 } }}
        busy={false}
        feedbackMax={1000}
        onApprove={onApprove}
        onReject={onReject}
      />,
    );
    expect(screen.getByText("No revisions left: rejecting cancels the run.")).toBeInTheDocument();
  });
});

describe("RunDetails", () => {
  it("metadata, per-agent table and transcript for a web run (no deliveries)", () => {
    render(<RunDetails run={completedRun} />);
    expect(screen.queryByRole("list", { name: "Transcript" })).toBeNull(); // loaded when opened
    openDetails("Run details");
    const meta = screen.getByLabelText("Run metadata");
    expect(meta).toHaveTextContent("demo mode (no LLM)");
    expect(meta).toHaveTextContent("8 real · 0 cached");
    expect(meta).toHaveTextContent("Tool calls:1");
    const table = screen.getByRole("region", { name: "Per-agent usage" });
    expect(within(table).getAllByRole("row").map((r) => r.textContent?.split(/\d/)[0])).toEqual([
      "AgentCallsTokensTime",
      "Supervisor",
      "Researcher",
      "Writer",
      "Reviewer",
    ]);
    expect(screen.queryByRole("region", { name: "Deliveries" })).toBeNull();
    const transcript = screen.getByRole("list", { name: "Transcript" });
    expect(within(transcript).getAllByText(/^Supervisor · decision \d of 5$/)).toHaveLength(4);
    expect(within(transcript).getByText("Verdict:")).toBeInTheDocument();
  });

  it("deliveries for a callback / Telegram run", async () => {
    mocked.deliveries.mockResolvedValue([delivery, { ...delivery, id: "msg_2", status: "retrying", last_status: 503, last_error: "busy", delivered_at: null, next_attempt_at: "2026-10-11T12:05:00Z" }]);
    const { unmount } = render(<RunDetails run={runView({ channel: "api" })} />);
    openDetails("Run details");
    const table = await screen.findByRole("region", { name: "Deliveries" });
    expect(table).toHaveTextContent("callback · example.com");
    expect(table).toHaveTextContent("HTTP 200");
    expect(table).toHaveTextContent("HTTP 503 busy");
    unmount();
    mocked.deliveries.mockResolvedValue([]);
    const second = render(<RunDetails run={runView({ channel: "telegram" })} />);
    openDetails("Run details");
    expect(await screen.findByText("No deliveries for this run.")).toBeInTheDocument();
    second.unmount();
    mocked.deliveries.mockRejectedValue(new ApiError("not_found", "Not found."));
    render(<RunDetails run={runView({ channel: "api" })} />);
    openDetails("Run details");
    expect(await screen.findByText("Not found.")).toBeInTheDocument();
  });

  it("transcript: fact check, feedback, repaired / forced decisions, unknown actions", () => {
    const h = completedRun.conversation_history;
    const review = h.find((e) => e.agent === "Reviewer" && e.action === "RESULT")!;
    const delegate = h[0];
    const extra: Entry[] = [
      { ...delegate, seq: 20, repaired: true } as Entry,
      { ...review, seq: 21, agent: "Fact-Checker", data: { claims: [{ sentence: "S1", status: "supported", evidence_ids: ["E1"], note: "" }, { sentence: "S2", status: "contradicted", evidence_ids: [], note: "Wrong" }] } } as Entry,
      { seq: 22, iteration: 4, agent: "Human", action: "FEEDBACK", text: "Shorter please", llm_calls: 0, cached: false } as Entry,
      { seq: 23, iteration: 5, agent: "Supervisor", action: "FINAL", reason: "out of decisions", forced: true, llm_calls: 1, cached: true, duration_ms: 5 } as Entry,
      { seq: 24, iteration: 5, agent: "System", action: "HANDOFF", text: "something new", llm_calls: 0, cached: false } as Entry,
    ];
    render(<Transcript run={runView({ conversation_history: [...h, ...extra] })} />);
    expect(screen.getByText("reply repaired")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Fact check" })).toHaveTextContent("contradicted");
    expect(screen.getByText("Shorter please")).toBeInTheDocument();
    expect(screen.getByText("Supervisor · forced decision")).toBeInTheDocument();
    expect(screen.getByText(/System · HANDOFF/)).toBeInTheDocument();
    expect(screen.getAllByText(/cached/).length).toBeGreaterThan(0);
  });

  it("transcript of an empty run", () => {
    render(<Transcript run={partial(0)} />);
    expect(screen.getByText(/the Supervisor is about to decide/)).toBeInTheDocument();
  });
});

describe("ServiceBanner", () => {
  it("states: full (demo label only), reduced with reason, offline, embedder failed", () => {
    const { rerender, container } = render(<ServiceBanner level="full" health={{ ...health, llm_mode: "gemini" }} retryAt={null} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<ServiceBanner level="reduced" health={{ ...health, service_reason: "web search down" }} retryAt={null} />);
    expect(screen.getByText(/Some sources are unavailable \(web search down\)/)).toBeInTheDocument();
    expect(screen.getByText(/Demo mode:/)).toBeInTheDocument();
    rerender(<ServiceBanner level="offline" health={null} retryAt={null} />);
    expect(screen.getByText(/can't be reached right now/)).toBeInTheDocument();
    rerender(<ServiceBanner level="full" health={{ ...health, embedder: { mode: "onnx", status: "failed" } }} retryAt={null} />);
    expect(screen.getByText("Document search is unavailable right now.")).toBeInTheDocument();
  });
});

describe("LibraryApp", () => {
  beforeEach(() => {
    mocked.listDocuments.mockResolvedValue({ workspace: null, documents: [], demo: documentList.demo });
    mocked.getDocument.mockReset();
    mocked.deleteDocument.mockReset();
    mocked.uploadDocument.mockReset();
  });

  it("lists the demo library (read-only) and the empty workspace", async () => {
    render(<LibraryApp />);
    expect(await screen.findByText("No uploads yet.")).toBeInTheDocument();
    const demo = screen.getByRole("region", { name: "Demo library" });
    expect(within(demo).getAllByRole("listitem").length).toBe(documentList.demo.length);
    expect(within(demo).queryByRole("button", { name: /Delete/ })).toBeNull();
    expect(within(demo).getByRole("listitem", { name: "nist-ai-rmf-1.0.pdf" })).toHaveTextContent("PDF · 1.9 MB · 48 pages · 108 chunks");
    expect(mocked.listDocuments).toHaveBeenCalledWith(WS);
    expect(screen.getByText(/expire after 24 hours/)).toBeInTheDocument();
  });

  it("upload → indexing → polling every 2 s → ready", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mocked.uploadDocument.mockImplementation(async (_f, _ws, onProgress) => {
      onProgress?.(0.5);
      return doc({ status: "indexing", chunks: 0 });
    });
    mocked.getDocument.mockResolvedValueOnce(doc({ status: "indexing", chunks: 0 })).mockResolvedValue(doc({ status: "ready" }));
    render(<LibraryApp />);
    await screen.findByText("No uploads yet.");
    const file = new File(["# Notes\n\nzebra"], "notes.md", { type: "text/markdown" });
    fireEvent.change(screen.getByLabelText("Choose files"), { target: { files: [file] } });
    const row = await screen.findByRole("listitem", { name: "notes.md" });
    expect(row).toHaveAttribute("data-status", "indexing");
    expect(within(row).getByRole("button", { name: "Delete notes.md" })).toBeDisabled();
    await act(() => vi.advanceTimersByTimeAsync(2100));
    await act(() => vi.advanceTimersByTimeAsync(2100));
    await waitFor(() => expect(screen.getByRole("listitem", { name: "notes.md" })).toHaveAttribute("data-status", "ready"));
    expect(mocked.getDocument).toHaveBeenCalledWith("doc_aaaa", WS);
    const calls = mocked.getDocument.mock.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(4100));
    expect(mocked.getDocument.mock.calls.length).toBe(calls); // polling stops at a terminal status
  });

  it("failed indexing shows the reason; a polled document that vanished is dropped", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mocked.listDocuments.mockResolvedValue({
      workspace: null,
      documents: [doc({ id: "doc_1", name: "a.pdf", status: "indexing" }), doc({ id: "doc_2", name: "b.md", status: "queued" })],
      demo: [],
    });
    mocked.getDocument.mockImplementation(async (id) => {
      if (id === "doc_1") return doc({ id: "doc_1", name: "a.pdf", status: "failed", error: "The PDF has no text layer." });
      throw new ApiError("not_found", "gone");
    });
    render(<LibraryApp />);
    await screen.findByRole("listitem", { name: "a.pdf" });
    await act(() => vi.advanceTimersByTimeAsync(2100));
    expect(await screen.findByText("The PDF has no text layer.")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("listitem", { name: "b.md" })).toBeNull());
  });

  it("client checks: type, size, count, total; server errors per file", async () => {
    mocked.listDocuments.mockResolvedValue({
      workspace: null,
      documents: Array.from({ length: 9 }, (_, i) => doc({ id: `doc_${i}`, name: `d${i}.md`, size_bytes: 2 * 1024 * 1024 })),
      demo: [],
    });
    mocked.uploadDocument.mockRejectedValue(new ApiError("unsupported_type", "Only .md, .txt and .pdf files are accepted (checked by content)."));
    render(<LibraryApp />);
    await screen.findByRole("listitem", { name: "d0.md" });
    const input = screen.getByLabelText("Choose files");
    const files = [
      new File(["x"], "virus.exe"),
      new File([], "empty.md"),
      new File([new Uint8Array(6 * 1024 * 1024)], "big.pdf"),
      new File(["x".repeat(3 * 1024 * 1024)], "toomuch.md"),
      new File(["fake pdf"], "fake.pdf"),
    ];
    fireEvent.change(input, { target: { files } });
    expect(await screen.findByText("Only .md, .txt and .pdf files are accepted.")).toBeInTheDocument();
    expect(screen.getByText("The file is empty.")).toBeInTheDocument();
    expect(screen.getByText("The file is larger than 5.0 MB.")).toBeInTheDocument();
    expect(await screen.findByText("Your workspace would exceed 20.0 MB.")).toBeInTheDocument();
    expect(await screen.findByText(/checked by content/)).toBeInTheDocument();
    expect(mocked.uploadDocument).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getAllByRole("button", { name: "Dismiss" })[0]);
    expect(screen.queryByText("Only .md, .txt and .pdf files are accepted.")).toBeNull();
  });

  it("the 10-document limit; drag and drop; rate-limit countdown", async () => {
    mocked.listDocuments.mockResolvedValue({
      workspace: null,
      documents: Array.from({ length: 10 }, (_, i) => doc({ id: `doc_${i}`, name: `d${i}.md` })),
      demo: [],
    });
    render(<LibraryApp />);
    const zone = (await screen.findByText(/Drag and drop files here/)).parentElement!;
    fireEvent.dragOver(zone);
    fireEvent.drop(zone, { dataTransfer: { files: [new File(["x"], "n.md")] } });
    expect(await screen.findByText(/already has 10 documents/)).toBeInTheDocument();
    mocked.listDocuments.mockResolvedValue({ workspace: null, documents: [], demo: [] });
    mocked.uploadDocument.mockRejectedValue(new ApiError("rate_limited", "Too many requests. Please wait a moment.", 60));
    cleanupAndRender();
    const input = await screen.findByLabelText("Choose files");
    await waitFor(() => expect(screen.getByText("No uploads yet.")).toBeInTheDocument());
    fireEvent.change(input, { target: { files: [new File(["x"], "n.md")] } });
    expect(await screen.findByText(/You can try again in (59|60) s|1 min/)).toBeInTheDocument();
  });

  it("workspace limits from GET /documents override the defaults", async () => {
    mocked.listDocuments.mockResolvedValue({
      ...documentListUpload,
      workspace: { ...documentListUpload.workspace!, limits: { max_documents: 1, max_bytes: 1024 } },
    });
    render(<LibraryApp />);
    expect(await screen.findByText(/1 documents and 1 KB per workspace/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Choose files"), { target: { files: [new File(["x"], "n.md")] } });
    expect(await screen.findByText(/already has 1 documents/)).toBeInTheDocument();
  });

  it("delete with confirmation; cancel; errors", async () => {
    mocked.listDocuments.mockResolvedValue({ workspace: null, documents: [doc()], demo: [] });
    render(<LibraryApp />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Delete notes.md" }));
    expect(screen.getByRole("group", { name: "Confirm deleting notes.md" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(mocked.deleteDocument).not.toHaveBeenCalled();
    mocked.deleteDocument.mockRejectedValueOnce(new ApiError("server", "Something went wrong on the server."));
    await user.click(screen.getByRole("button", { name: "Delete notes.md" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("Something went wrong on the server.")).toBeInTheDocument();
    mocked.deleteDocument.mockResolvedValueOnce(undefined);
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("No uploads yet.")).toBeInTheDocument();
    expect(mocked.deleteDocument).toHaveBeenLastCalledWith("doc_aaaa", WS);
  });

  it("a delete of a document that is already gone removes it; a failed list shows the error", async () => {
    mocked.listDocuments.mockResolvedValue({ workspace: null, documents: [doc()], demo: [] });
    mocked.deleteDocument.mockRejectedValue(new ApiError("not_found", "gone"));
    const { unmount } = render(<LibraryApp />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Delete notes.md" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("No uploads yet.")).toBeInTheDocument();
    unmount();
    mocked.listDocuments.mockRejectedValue(new ApiError("network", "Can't reach the research service."));
    render(<LibraryApp />);
    expect(await screen.findByText("Can't reach the research service.")).toBeInTheDocument();
  });
});

function cleanupAndRender() {
  // a fresh LibraryApp in the same test (the previous one is unmounted)
  document.body.innerHTML = "";
  render(<LibraryApp />);
}
