"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Composer from "@/components/chat/Composer";
import History from "@/components/chat/History";
import RunSettings, { DEFAULT_SETTINGS, type Settings } from "@/components/chat/RunSettings";
import Suggestions from "@/components/chat/Suggestions";
import Turn from "@/components/chat/Turn";
import ErrorBanner from "@/components/ErrorBanner";
import ServiceBanner from "@/components/ServiceBanner";
import { useApiError } from "@/hooks/useApiError";
import { useHealth } from "@/hooks/useHealth";
import { type RunSource, useRun } from "@/hooks/useRun";
import { threadStore, useThread } from "@/hooks/useThread";
import { useWorkspace } from "@/hooks/useWorkspace";
import { agentTitle, working } from "@/lib/agents";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { type Agent, type Limits, type ResearchView, type Sample, TERMINAL } from "@/lib/schemas";
import { addToThread, isRunId, removeFromThread } from "@/lib/thread";

const FALLBACK_LIMITS: Limits = {
  question_min_chars: 10,
  question_max_chars: 2000,
  default_max_iterations: 5,
  max_iterations_limit: 8,
  feedback_max_chars: 1000,
};
const REPLAY_ID = "replay";

function setRunInUrl(runId: string | null) {
  const url = new URL(window.location.href);
  if (runId) url.searchParams.set("run", runId);
  else url.searchParams.delete("run");
  window.history.replaceState(null, "", url);
}

export default function ResearchApp({ initialRunId }: { initialRunId: string | null }) {
  const [thread, setThread] = useThread();
  const workspaceId = useWorkspace();
  const [views, setViews] = useState<Record<string, ResearchView>>({});
  const [missing, setMissing] = useState<Record<string, true>>({});
  const [agents, setAgents] = useState<Agent[]>([]);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [replay, setReplay] = useState<ResearchView | null>(null);
  const [question, setQuestion] = useState("");
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [posting, setPosting] = useState(false);
  const [acting, setActing] = useState(false);
  const { error, cooldown, fail, clear } = useApiError();
  const { health, level, retryAt, reload } = useHealth();
  const requested = useRef(new Set<string>());

  const limits = health?.limits ?? FALLBACK_LIMITS;

  const failAndCheck = useCallback(
    (err: unknown) => {
      fail(err);
      if (err instanceof ApiError && (err.kind === "busy" || err.kind === "quota")) reload();
    },
    [fail, reload],
  );

  // ---- the thread: ?run= joins it; every run but the last is loaded once ---------------
  useEffect(() => {
    if (initialRunId && isRunId(initialRunId)) {
      // The store, not `thread`: during hydration `thread` is still the empty server snapshot.
      const items = threadStore.get();
      if (!items.some((i) => i.run_id === initialRunId)) {
        threadStore.set(addToThread(items, { run_id: initialRunId, question: "", created_at: new Date().toISOString() }));
      }
    }
  }, [initialRunId]);

  useEffect(() => {
    api.agents().then(setAgents, () => {});
    api.samples().then(setSamples, () => {});
  }, []);

  const last = thread.at(-1) ?? null;
  useEffect(() => {
    for (const item of thread.slice(0, -1)) {
      if (requested.current.has(item.run_id)) continue;
      requested.current.add(item.run_id);
      api.getResearch(item.run_id).then(
        (v) => setViews((all) => ({ ...all, [v.run_id]: v })),
        (err) => {
          if (err instanceof ApiError && err.kind === "not_found") {
            setMissing((m) => ({ ...m, [item.run_id]: true }));
          }
        },
      );
    }
  }, [thread]);

  // ---- the live run (the last one) and the sample replay ----------------------------------
  const liveId = last && !missing[last.run_id] ? last.run_id : null;
  const liveSource = useMemo<RunSource | null>(() => (liveId ? { kind: "live", id: liveId } : null), [liveId]);
  const onLiveView = useCallback((v: ResearchView) => setViews((all) => ({ ...all, [v.run_id]: v })), []);
  const live = useRun(liveSource, onLiveView);
  const replaySource = useMemo<RunSource | null>(() => (replay ? { kind: "replay", view: replay } : null), [replay]);
  const replayRun = useRun(replaySource);

  const liveMissing = live.error?.kind === "not_found";
  const viewOf = (id: string): ResearchView | null => (id === liveId ? live.view : null) ?? views[id] ?? null;
  const lastView = liveId ? viewOf(liveId) : null;
  const activeRun = lastView !== null && !TERMINAL.has(lastView.status) && lastView.status !== "awaiting_approval";

  // ---- actions -----------------------------------------------------------------------------
  async function submit(text = question) {
    const clean = text.trim();
    if (!workspaceId) return;
    setPosting(true);
    clear();
    try {
      const accepted = await api.startResearch(
        {
          question: clean,
          depth: settings.depth,
          sources: settings.sources,
          ...(settings.max_iterations ? { max_iterations: settings.max_iterations } : {}),
          require_approval: settings.require_approval,
          use_memory: settings.use_memory,
        },
        workspaceId,
      );
      setThread(
        addToThread(thread, {
          run_id: accepted.run_id,
          question: clean,
          depth: settings.depth,
          sources: settings.sources,
          created_at: new Date().toISOString(),
        }),
      );
      setQuestion("");
      setReplay(null);
      setRunInUrl(accepted.run_id);
    } catch (err) {
      failAndCheck(err);
    } finally {
      setPosting(false);
    }
  }

  async function act(runId: string, action: () => Promise<ResearchView>) {
    setActing(true);
    clear();
    try {
      const v = await action();
      setViews((all) => ({ ...all, [v.run_id]: v }));
    } catch (err) {
      failAndCheck(err);
      api.getResearch(runId).then(onLiveView, () => {});
    } finally {
      setActing(false);
    }
  }

  async function startReplay(s: Sample) {
    clear();
    try {
      setReplay(await api.sample(s.id));
      requestAnimationFrame(() => document.getElementById(`turn-${REPLAY_ID}`)?.scrollIntoView?.({ block: "start" }));
    } catch (err) {
      failAndCheck(err);
    }
  }

  function remove(runId: string) {
    setThread(removeFromThread(thread, runId));
    if (thread.at(-1)?.run_id === runId) setRunInUrl(null);
  }

  // ---- composer state ----------------------------------------------------------------------
  const length = question.trim().length;
  const disabledReason = posting
    ? "Sending…"
    : activeRun
      ? "Wait for the current run to finish."
      : level === "minimal"
        ? "Today's AI quota is used up."
        : cooldown > 0
          ? `You can send again in ${formatDuration(cooldown)}.`
          : settings.sources.length === 0
            ? "Choose at least one source."
            : length < limits.question_min_chars
              ? `Write at least ${limits.question_min_chars} characters.`
              : length > limits.question_max_chars
                ? `Keep the question under ${limits.question_max_chars} characters.`
                : !workspaceId
                  ? "Loading…"
                  : null;

  // ---- one polite status line (not a live log: it would read whole reports aloud) ----------
  const statusLine = (() => {
    if (!lastView) return "";
    if (lastView.status === "awaiting_approval") return "Waiting for your approval.";
    if (lastView.status === "completed") return "Report ready.";
    if (lastView.status === "failed") return "The run failed.";
    const names = working(live.board);
    return names.length ? `${names.map(agentTitle).join(" and ")} working.` : "Working.";
  })();

  return (
    <div className="flex flex-1 flex-col gap-4">
      <ServiceBanner level={level} health={health} retryAt={retryAt} />
      <p role="status" className="sr-only">
        {statusLine}
      </p>

      <div className="grid flex-1 grid-cols-1 gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
        <aside aria-label="Sidebar" className="min-w-0 lg:sticky lg:top-4 lg:self-start">
          <History items={thread} onRemove={remove} />
        </aside>

        <section aria-label="Conversation" className="flex min-w-0 flex-col gap-4">
          {thread.length === 0 && !replay && (
            <div className="space-y-2 rounded-lg border border-dashed border-zinc-400 p-4 text-sm dark:border-zinc-600">
              <p className="font-medium">Ask a research question.</p>
              <p className="text-zinc-700 dark:text-zinc-300">
                A Supervisor delegates to a Researcher (who chooses its own web, Wikipedia and document searches), a
                Writer, a Reviewer and a Fact-Checker. Watch every tool call live, then read a cited report.
              </p>
            </div>
          )}
          <ol className="flex flex-col gap-8">
            {thread.map((item) => {
              const isLive = item.run_id === liveId;
              return (
                <Turn
                  key={item.run_id}
                  id={`turn-${item.run_id}`}
                  question={item.question}
                  view={viewOf(item.run_id)}
                  missing={Boolean(missing[item.run_id]) || (isLive && liveMissing)}
                  board={isLive && live.view && !TERMINAL.has(live.view.status) ? live.board : null}
                  lost={isLive && live.lost}
                  sample={false}
                  busy={acting}
                  feedbackMax={limits.feedback_max_chars}
                  agents={agents}
                  activityOpen={isLive && !replay}
                  onApprove={() => act(item.run_id, () => api.approve(item.run_id))}
                  onReject={(feedback) => act(item.run_id, () => api.reject(item.run_id, feedback))}
                  onRemove={() => remove(item.run_id)}
                />
              );
            })}
            {replay && (
              <Turn
                id={`turn-${REPLAY_ID}`}
                question={replay.question}
                view={replayRun.view}
                missing={false}
                board={replayRun.board}
                lost={false}
                sample
                busy={false}
                feedbackMax={limits.feedback_max_chars}
                agents={agents}
                activityOpen
                onApprove={() => {}}
                onReject={() => {}}
                onRemove={() => setReplay(null)}
                onRunForReal={() => submit(replay.question)}
              />
            )}
          </ol>
          {live.error && !liveMissing && <ErrorBanner error={live.error} cooldown={0} />}
          {error && <ErrorBanner error={error} cooldown={cooldown} />}
          <Suggestions samples={samples} disabled={posting} onReplay={startReplay} />
          <Composer
            value={question}
            maxChars={limits.question_max_chars}
            disabledReason={disabledReason}
            busy={posting}
            onChange={setQuestion}
            onSubmit={() => submit()}
          >
            <RunSettings
              value={settings}
              maxIterations={limits.max_iterations_limit}
              searchAvailable={health?.search?.available ?? true}
              indexWarming={health?.embedder?.status === "loading"}
              onChange={setSettings}
            />
          </Composer>
        </section>
      </div>
    </div>
  );
}
