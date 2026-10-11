/** The chats kept in the browser (storage v3, plan Improving_Chat_UI_UX §8.1): conversation
 * ids, their turns (run ids, questions, run settings) and the current chat. The runs and the
 * conversations live on the server; this is only an index. v2 items (one run each) are
 * imported once as read-only chats. Storage can be missing or blocked (private mode): every
 * access is guarded. */
import { z } from "zod";
import { type Conversation, CONVERSATION_ID, type ResearchView } from "./schemas";

export const CHATS_KEY = "research-chats-v3";
export const LEGACY_THREAD_KEY = "research-thread-v2";
export const MAX_CHATS = 30;
const MAX_TURNS_KEPT = 100; // the server caps a chat at 30; this only bounds storage

const RUN_ID = /^run_[a-f0-9]+$/;

const TurnSchema = z.object({
  run_id: z.string().regex(RUN_ID),
  turn: z.number().int().min(1),
  question: z.string(),
  depth: z.string().optional(),
  sources: z.array(z.string()).optional(),
  created_at: z.string(),
});
export type ChatTurn = z.infer<typeof TurnSchema>;

const ChatSchema = z.object({
  conversation_id: z.string().regex(CONVERSATION_ID),
  title: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  turns: z.array(TurnSchema),
  // Imported from v2 or gone from the server: it can be read, and a new question starts a
  // new chat.
  read_only: z.boolean().optional(),
});
export type Chat = z.infer<typeof ChatSchema>;

export interface ChatState {
  current: string | null; // null: a new, empty chat
  chats: Chat[]; // newest first
}

export const EMPTY: ChatState = { current: null, chats: [] };

const LegacyItemSchema = z.object({
  run_id: z.string().regex(RUN_ID),
  question: z.string(),
  depth: z.string().optional(),
  sources: z.array(z.string()).optional(),
  created_at: z.string(),
});

export const isRunId = (value: string): boolean => RUN_ID.test(value);
export const isConversationId = (value: string): boolean => CONVERSATION_ID.test(value);

/** The id the backend gives a run stored before v1.2 (`legacy_conversation_id`). */
export function legacyConversationId(runId: string): string {
  return `conv_${`${runId.replace(/^run_/, "")}0000`.slice(0, 16).padEnd(16, "0")}`;
}

function storage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

/** Keeps the parts that parse: one bad chat or turn does not lose the rest. */
function parseChats(items: unknown[]): Chat[] {
  const chats: Chat[] = [];
  for (const item of items) {
    const turns = z.object({ turns: z.array(z.unknown()) }).safeParse(item);
    if (!turns.success) continue;
    const good = turns.data.turns.flatMap((t) => {
      const r = TurnSchema.safeParse(t);
      return r.success ? [r.data] : [];
    });
    const chat = ChatSchema.safeParse({ ...(item as object), turns: good });
    if (chat.success) chats.push(chat.data);
  }
  return chats;
}

/** v2 → v3: one read-only chat per old run. */
export function importLegacy(raw: string | null | undefined): Chat[] {
  if (!raw) return [];
  try {
    const parsed = z.array(z.unknown()).safeParse(JSON.parse(raw));
    if (!parsed.success) return [];
    return parsed.data.flatMap((i) => {
      const r = LegacyItemSchema.safeParse(i);
      if (!r.success) return [];
      const { run_id, question, depth, sources, created_at } = r.data;
      return [
        {
          conversation_id: legacyConversationId(run_id),
          title: question,
          created_at,
          updated_at: created_at,
          turns: [{ run_id, turn: 1, question, depth, sources, created_at }],
          read_only: true,
        },
      ];
    });
  } catch {
    return [];
  }
}

export function loadChats(): ChatState {
  const store = storage();
  try {
    const raw = store?.getItem(CHATS_KEY);
    if (raw) {
      const parsed = z
        .object({ current: z.string().nullable().optional(), chats: z.array(z.unknown()) })
        .safeParse(JSON.parse(raw));
      if (!parsed.success) return EMPTY;
      const chats = sortChats(parseChats(parsed.data.chats));
      const current = parsed.data.current ?? null;
      return { current: chats.some((c) => c.conversation_id === current) ? current : null, chats };
    }
    const imported = importLegacy(store?.getItem(LEGACY_THREAD_KEY));
    if (imported.length === 0) return EMPTY;
    const state: ChatState = { current: null, chats: sortChats(imported) };
    saveChats(state);
    store?.removeItem(LEGACY_THREAD_KEY); // imported once: removing a chat must not bring it back
    return state;
  } catch {
    return EMPTY;
  }
}

export function saveChats(state: ChatState): void {
  try {
    storage()?.setItem(CHATS_KEY, JSON.stringify(state));
  } catch {
    // Quota or blocked storage: the chats just aren't remembered.
  }
}

/** Newest first; at most MAX_CHATS (the current chat is never dropped). */
function sortChats(chats: Chat[], keep: string | null = null): Chat[] {
  const sorted = [...chats].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  if (sorted.length <= MAX_CHATS) return sorted;
  const kept = sorted.slice(0, MAX_CHATS);
  const current = sorted.find((c) => c.conversation_id === keep);
  if (current && !kept.includes(current)) kept[MAX_CHATS - 1] = current;
  return kept;
}

export function findChat(state: ChatState, id: string | null): Chat | null {
  return id ? (state.chats.find((c) => c.conversation_id === id) ?? null) : null;
}

export function currentChat(state: ChatState): Chat | null {
  return findChat(state, state.current);
}

export function setCurrent(state: ChatState, id: string | null): ChatState {
  return { ...state, current: id };
}

/** Adds or replaces a chat (by id). */
export function upsertChat(state: ChatState, chat: Chat): ChatState {
  const others = state.chats.filter((c) => c.conversation_id !== chat.conversation_id);
  return { ...state, chats: sortChats([...others, chat], state.current) };
}

export function removeChat(state: ChatState, id: string): ChatState {
  return {
    current: state.current === id ? null : state.current,
    chats: state.chats.filter((c) => c.conversation_id !== id),
  };
}

const byTurn = (a: ChatTurn, b: ChatTurn) => a.turn - b.turn || a.created_at.localeCompare(b.created_at);

/** A new turn (a submitted question): into its chat, created when new; the chat becomes current. */
export function addTurn(state: ChatState, conversationId: string, turn: ChatTurn): ChatState {
  const chat = findChat(state, conversationId);
  const turns = [...(chat?.turns ?? []).filter((t) => t.run_id !== turn.run_id), turn].sort(byTurn).slice(-MAX_TURNS_KEPT);
  const next: Chat = {
    conversation_id: conversationId,
    title: chat?.title || turns[0].question,
    created_at: chat?.created_at ?? turn.created_at,
    updated_at: turn.created_at,
    turns,
  };
  return upsertChat({ ...state, current: conversationId }, next);
}

/** The server's view of a chat (`GET /conversations/{id}`) is the truth for its turns; the
 * sources chosen in this browser are kept. */
export function mergeConversation(state: ChatState, conv: Conversation): ChatState {
  const local = findChat(state, conv.conversation_id);
  const known = new Map((local?.turns ?? []).map((t) => [t.run_id, t]));
  const server = new Set(conv.turns.map((t) => t.run_id));
  const turns = [
    ...conv.turns.map((t) => ({
      run_id: t.run_id,
      turn: t.turn,
      question: t.question,
      depth: t.depth ?? known.get(t.run_id)?.depth,
      sources: known.get(t.run_id)?.sources,
      created_at: t.created_at,
    })),
    // a turn submitted here while the request was in flight is kept
    ...(local?.turns ?? []).filter((t) => !server.has(t.run_id)),
  ]
    .filter((t) => isRunId(t.run_id))
    .sort(byTurn)
    .slice(-MAX_TURNS_KEPT);
  if (turns.length === 0) return state;
  const chat: Chat = {
    conversation_id: conv.conversation_id,
    title: local?.title || turns[0].question,
    created_at: local?.created_at ?? turns[0].created_at,
    updated_at: turns.reduce((at, t) => (t.created_at > at ? t.created_at : at), local?.updated_at ?? ""),
    turns,
  };
  return upsertChat(state, chat);
}

/** A run reached by a link whose chat the server cannot give (an older backend): a
 * read-only chat of its own. */
export function chatFromView(view: ResearchView, at: string): Chat {
  return {
    conversation_id: view.conversation_id ?? legacyConversationId(view.run_id),
    title: view.question,
    created_at: view.metadata.created_at || at,
    updated_at: view.metadata.created_at || at,
    turns: [
      {
        run_id: view.run_id,
        turn: view.turn ?? 1,
        question: view.question,
        depth: view.depth,
        sources: view.sources,
        created_at: view.metadata.created_at || at,
      },
    ],
    read_only: true,
  };
}

export function markReadOnly(state: ChatState, id: string): ChatState {
  const chat = findChat(state, id);
  if (!chat || chat.read_only) return state;
  return { ...state, chats: state.chats.map((c) => (c === chat ? { ...c, read_only: true } : c)) };
}

/** The chat that holds a run, if any. */
export function chatOfRun(state: ChatState, runId: string): Chat | null {
  return state.chats.find((c) => c.turns.some((t) => t.run_id === runId)) ?? null;
}
