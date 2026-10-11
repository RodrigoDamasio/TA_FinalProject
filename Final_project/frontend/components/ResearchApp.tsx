"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ChatList from "@/components/chat/ChatList";
import Composer from "@/components/chat/Composer";
import RunSettings, { DEFAULT_SETTINGS, type Settings } from "@/components/chat/RunSettings";
import Suggestions from "@/components/chat/Suggestions";
import Turn from "@/components/chat/Turn";
import ErrorBanner from "@/components/ErrorBanner";
import ServiceBanner from "@/components/ServiceBanner";
import { useApiError } from "@/hooks/useApiError";
import { chatStore, collapseStore, useChats, useCollapse } from "@/hooks/useChats";
import { useHealth } from "@/hooks/useHealth";
import { type RunSource, useRun } from "@/hooks/useRun";
import { useWorkspace } from "@/hooks/useWorkspace";
import { agentTitle, working } from "@/lib/agents";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import {
  addTurn,
  chatFromView,
  chatOfRun,
  currentChat,
  findChat,
  isConversationId,
  isRunId,
  legacyConversationId,
  markReadOnly,
  mergeConversation,
  removeChat,
  setCurrent,
  upsertChat,
} from "@/lib/chats";
import { isExpanded, resetRuns, setAll } from "@/lib/collapse";
import { formatDuration, shorten } from "@/lib/format";
import { type Agent, type Limits, type ResearchView, type Sample, TERMINAL } from "@/lib/schemas";

const FALLBACK_LIMITS: Limits = {
  question_min_chars: 10,
  question_max_chars: 2000,
  default_max_iterations: 5,
  max_iterations_limit: 8,
  feedback_max_chars: 1000,
};
// v1.2 contract values (BACKEND_PLAN §26.1), used while /health does not send them.
const FOLLOWUP_MIN_CHARS = 2;
const CONVERSATION_MAX_TURNS = 30;
const REPLAY_ID = "replay";

/** `?chat=<id>` (and `&run=<id>` for a turn); replaceState keeps Next's router in sync. */
function setChatInUrl(chatId: string | null, runId: string | null = null) {
  const url = new URL(window.location.href);
  url.searchParams.delete("run");
  url.searchParams.delete("chat");
  if (chatId) url.searchParams.set("chat", chatId);
  if (chatId && runId) url.searchParams.set("run", runId);
  window.history.replaceState(null, "", url);
}

export default function ResearchApp({
  initialChatId = null,
  initialRunId = null,
}: {
  initialChatId?: string | null;
  initialRunId?: string | null;
}) {
  const chats = useChats();
  const overrides = useCollapse();
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
  const [linkPending, setLinkPending] = useState(Boolean(initialChatId || initialRunId));
  const { error, cooldown, fail, clear } = useApiError();
  const { health, level, retryAt, reload } = useHealth();
  const requested = useRef(new Set<string>());
  const synced = useRef(new Set<string>());
  const focusRun = useRef<{ id: string; focus: boolean } | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  const limits = health?.limits ?? FALLBACK_LIMITS;
  const chat = currentChat(chats);
  const turns = useMemo(() => chat?.turns ?? [], [chat]);

  const failAndCheck = useCallback(
    (err: unknown) => {
      fail(err);
      if (err instanceof ApiError && (err.kind === "busy" || err.kind === "quota")) reload();
    },
    [fail, reload],
  );

  // ---- chats from the server: a link to an unknown chat, or a known one refreshed once ----
  const syncChat = useCallback(
    (id: string, open: boolean): Promise<void> => {
      synced.current.add(id);
      return api.conversation(id).then(
        (conv) =>
          chatStore.update((s) => {
            const merged = mergeConversation(s, conv);
            return open ? setCurrent(merged, id) : merged;
          }),
        (err) => {
          if (err instanceof ApiError && err.kind === "conversation_not_found") {
            chatStore.update((s) => markReadOnly(s, id));
          }
          if (open && !findChat(chatStore.get(), id)) failAndCheck(err);
        },
      );
    },
    [failAndCheck],
  );

  // ---- the link this page was opened with: ?chat= (and &run=), or ?run= alone ------------
  const linkHandled = useRef(false);
  useEffect(() => {
    if (linkHandled.current) return;
    linkHandled.current = true;
    const chatId = initialChatId && isConversationId(initialChatId) ? initialChatId : null;
    const runId = initialRunId && isRunId(initialRunId) ? initialRunId : null;
    const done = () => setLinkPending(false);
    if (runId) focusRun.current = { id: runId, focus: false };
    if (runId) collapseStore.update((o) => setAll(o, [runId], true));
    // The store, not `chats`: during hydration `chats` is still the empty server snapshot.
    const knownChat = chatId ? findChat(chatStore.get(), chatId) : runId ? chatOfRun(chatStore.get(), runId) : null;
    if (knownChat) {
      chatStore.update((s) => setCurrent(s, knownChat.conversation_id));
      Promise.resolve().then(done);
    } else if (chatId) {
      syncChat(chatId, true).finally(done);
    } else if (runId) {
      api
        .getResearch(runId)
        .then(async (view) => {
          setViews((all) => ({ ...all, [view.run_id]: view }));
          if (view.conversation_id) await syncChat(view.conversation_id, true);
          if (!findChat(chatStore.get(), view.conversation_id ?? legacyConversationId(view.run_id))) {
            const own = chatFromView(view, new Date().toISOString()); // an older backend
            chatStore.update((s) => setCurrent(upsertChat(s, own), own.conversation_id));
          }
        }, failAndCheck)
        .finally(done);
    } else {
      Promise.resolve().then(done);
    }
  }, [initialChatId, initialRunId, syncChat, failAndCheck]);

  // A chat opened from this browser's list is refreshed once (turns added elsewhere).
  const chatId = chat?.conversation_id ?? null;
  const readOnly = Boolean(chat?.read_only);
  useEffect(() => {
    if (chatId && !readOnly && !synced.current.has(chatId)) void syncChat(chatId, false);
  }, [chatId, readOnly, syncChat]);

  useEffect(() => {
    api.agents().then(setAgents, () => {});
    api.samples().then(setSamples, () => {});
  }, []);

  // ---- views: every turn but the last is loaded once; the last one is followed -------------
  const last = turns.at(-1) ?? null;
  useEffect(() => {
    for (const item of turns.slice(0, -1)) {
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
  }, [turns]);

  const liveId = last && !missing[last.run_id] ? last.run_id : null;
  const liveSource = useMemo<RunSource | null>(() => (liveId ? { kind: "live", id: liveId } : null), [liveId]);
  const onLiveView = useCallback((v: ResearchView) => setViews((all) => ({ ...all, [v.run_id]: v })), []);
  const live = useRun(liveSource, onLiveView);
  const replaySource = useMemo<RunSource | null>(() => (replay ? { kind: "replay", view: replay } : null), [replay]);
  const replayRun = useRun(replaySource);

  const liveMissing = live.error?.kind === "not_found";
  const viewOf = (id: string): ResearchView | null => (id === liveId ? live.view : null) ?? views[id] ?? null;
  const lastView = liveId ? viewOf(liveId) : null;

  // Focus (after a question) or scroll (a link to a turn) once the turn is on the page.
  useEffect(() => {
    const target = focusRun.current;
    if (!target) return;
    const el = document.getElementById(`turn-${target.id}`);
    if (!el) return;
    focusRun.current = null;
    if (target.focus) document.getElementById(`turn-${target.id}-toggle`)?.focus();
    el.scrollIntoView?.({ block: "start" });
  });

  // ---- actions -----------------------------------------------------------------------------
  const followUp = chat !== null && !chat.read_only && turns.length > 0;

  async function submit(text = question) {
    const clean = text.trim();
    if (!workspaceId) return;
    setPosting(true);
    clear();
    const conversationId = followUp ? chat.conversation_id : undefined;
    try {
      const accepted = await api.startResearch(
        {
          question: clean,
          depth: settings.depth,
          sources: settings.sources,
          ...(settings.max_iterations ? { max_iterations: settings.max_iterations } : {}),
          require_approval: settings.require_approval,
          use_memory: settings.use_memory,
          ...(conversationId ? { conversation_id: conversationId } : {}),
        },
        workspaceId,
      );
      const cid = accepted.conversation_id ?? legacyConversationId(accepted.run_id);
      if (conversationId) collapseStore.update((o) => resetRuns(o, turns.map((t) => t.run_id)));
      chatStore.update((s) =>
        addTurn(s, cid, {
          run_id: accepted.run_id,
          turn: accepted.turn ?? (conversationId ? turns.length + 1 : 1),
          question: clean,
          depth: settings.depth,
          sources: settings.sources,
          created_at: new Date().toISOString(),
        }),
      );
      synced.current.add(cid); // just created or continued here: nothing to refresh
      focusRun.current = { id: accepted.run_id, focus: true };
      setQuestion("");
      setReplay(null);
      setChatInUrl(cid);
    } catch (err) {
      failAndCheck(err);
      if (!(err instanceof ApiError) || !conversationId) return;
      if (err.kind === "conversation_not_found") chatStore.update((s) => markReadOnly(s, conversationId));
      if (err.kind === "conversation_busy" && liveId) api.getResearch(liveId).then(onLiveView, () => {});
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

  function newChat() {
    clear();
    setReplay(null);
    chatStore.update((s) => setCurrent(s, null));
    setChatInUrl(null);
    composerRef.current?.focus();
  }

  function openChat(id: string) {
    clear();
    setReplay(null);
    chatStore.update((s) => setCurrent(s, id));
    setChatInUrl(id);
  }

  function forgetChat(id: string) {
    if (chats.current === id) setChatInUrl(null);
    chatStore.update((s) => removeChat(s, id));
  }

  const runIds = turns.map((t) => t.run_id);
  const toggle = (runId: string, expanded: boolean) => collapseStore.update((o) => setAll(o, [runId], !expanded));
  const setEvery = (expanded: boolean) => collapseStore.update((o) => setAll(o, runIds, expanded));

  // ---- composer state ----------------------------------------------------------------------
  const minChars = followUp ? (limits.followup_min_chars ?? FOLLOWUP_MIN_CHARS) : limits.question_min_chars;
  const maxTurns = limits.conversation_max_turns ?? CONVERSATION_MAX_TURNS;
  const chatRunning = followUp && lastView !== null && !TERMINAL.has(lastView.status);
  const length = question.trim().length;
  const disabledReason = posting
    ? "Sending…"
    : chatRunning
      ? lastView?.status === "awaiting_approval"
        ? "Approve or reject the draft before asking a follow-up."
        : "Wait for the current answer to finish."
      : followUp && turns.length >= maxTurns
        ? "This chat is full. Start a new chat."
        : level === "minimal"
          ? "Today's AI quota is used up."
          : cooldown > 0
            ? `You can send again in ${formatDuration(cooldown)}.`
            : settings.sources.length === 0
              ? "Choose at least one source."
              : length < minChars
                ? `Write at least ${minChars} characters.`
                : length > limits.question_max_chars
                  ? `Keep the question under ${limits.question_max_chars} characters.`
                  : !workspaceId
                    ? "Loading…"
                    : null;
  const composerNote = chat?.read_only
    ? "This chat can only be read (saved before chats existed, or no longer on the server): your question starts a new chat."
    : null;
  const errorActions =
    error && (error.kind === "conversation_full" || error.kind === "conversation_not_found")
      ? [{ label: "Start a new chat", onClick: newChat }]
      : [];

  // ---- one polite status line (not a live log: it would read whole reports aloud) ----------
  const statusLine = (() => {
    if (!lastView) return "";
    if (lastView.status === "awaiting_approval") return "Waiting for your approval.";
    if (lastView.status === "completed") return "Report ready.";
    if (lastView.status === "failed") return "The run failed.";
    if (lastView.status === "cancelled") return "";
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
          <ChatList chats={chats.chats} current={chats.current} onOpen={openChat} onRemove={forgetChat} onNew={newChat} />
        </aside>

        <section aria-label="Conversation" className="flex min-w-0 flex-col gap-4">
          {chat && (
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-zinc-200 pb-2 dark:border-zinc-800">
              <h2 className="min-w-0 text-base font-semibold break-words">{shorten(chat.title, 120)}</h2>
              <div className="flex flex-wrap gap-3 text-sm font-medium">
                {turns.length > 1 && (
                  <>
                    <button type="button" onClick={() => setEvery(false)} className="text-orange-800 underline dark:text-orange-300">
                      Collapse all
                    </button>
                    <button type="button" onClick={() => setEvery(true)} className="text-orange-800 underline dark:text-orange-300">
                      Expand all
                    </button>
                  </>
                )}
                <button type="button" onClick={newChat} className="text-orange-800 underline dark:text-orange-300">
                  New chat
                </button>
              </div>
            </div>
          )}
          {!chat && !replay && (
            <div className="space-y-2 rounded-lg border border-dashed border-zinc-400 p-4 text-sm dark:border-zinc-600">
              {linkPending ? (
                <p className="font-medium">Loading the chat…</p>
              ) : (
                <>
                  <p className="font-medium">Ask a research question.</p>
                  <p className="text-zinc-700 dark:text-zinc-300">
                    A Supervisor delegates to a Researcher (who chooses its own web, Wikipedia and document searches), a
                    Writer, a Reviewer and a Fact-Checker. Watch every tool call live, then read a cited report. Ask
                    follow-ups in the same chat; start a new chat for a new topic.
                  </p>
                </>
              )}
            </div>
          )}
          <ol className="flex flex-col gap-6" aria-label="Turns">
            {turns.map((item, i) => {
              const isLive = item.run_id === liveId;
              const expanded = isExpanded(overrides, item.run_id, i === turns.length - 1);
              return (
                <Turn
                  key={item.run_id}
                  id={`turn-${item.run_id}`}
                  question={item.question}
                  turn={item.turn}
                  view={viewOf(item.run_id)}
                  missing={Boolean(missing[item.run_id]) || (isLive && liveMissing)}
                  board={isLive && live.view && !TERMINAL.has(live.view.status) ? live.board : null}
                  lost={isLive && live.lost}
                  sample={false}
                  busy={acting}
                  feedbackMax={limits.feedback_max_chars}
                  agents={agents}
                  expanded={expanded}
                  onToggle={() => toggle(item.run_id, expanded)}
                  activityOpen={false}
                  onApprove={() => act(item.run_id, () => api.approve(item.run_id))}
                  onReject={(feedback) => act(item.run_id, () => api.reject(item.run_id, feedback))}
                />
              );
            })}
            {replay && (
              <Turn
                id={`turn-${REPLAY_ID}`}
                question={replay.question}
                turn={null}
                view={replayRun.view}
                missing={false}
                board={replayRun.board}
                lost={false}
                sample
                busy={false}
                feedbackMax={limits.feedback_max_chars}
                agents={agents}
                expanded
                activityOpen
                onApprove={() => {}}
                onReject={() => {}}
                onRunForReal={() => submit(replay.question)}
              />
            )}
          </ol>
          {live.error && !liveMissing && <ErrorBanner error={live.error} cooldown={0} />}
          {error && <ErrorBanner error={error} cooldown={cooldown} actions={errorActions} />}
          {!chat && <Suggestions samples={samples} disabled={posting} onReplay={startReplay} />}
          <Composer
            ref={composerRef}
            value={question}
            maxChars={limits.question_max_chars}
            disabledReason={disabledReason}
            busy={posting}
            placeholder={followUp ? "Ask a follow-up…" : undefined}
            note={composerNote}
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
