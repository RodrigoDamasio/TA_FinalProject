"use client";
import { useState } from "react";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useNow } from "@/hooks/useNow";
import type { Chat } from "@/lib/chats";
import { formatRelative, plural, shorten } from "@/lib/format";

interface Props {
  chats: Chat[]; // newest first
  current: string | null;
  onOpen: (conversationId: string) => void;
  onRemove: (conversationId: string) => void;
  onNew: () => void;
}

/** The chats kept in this browser: open one, start a new one, or remove one from this browser
 * (the server keeps it). A list in the sidebar on wide screens, a drawer on phones. */
export default function ChatList({ chats, current, onOpen, onRemove, onNew }: Props) {
  const wide = useMediaQuery("(min-width: 1024px)");
  const now = useNow();
  const [confirming, setConfirming] = useState<string | null>(null);

  const newButton = (
    <button
      type="button"
      onClick={onNew}
      className="w-full rounded-md border border-orange-700 px-3 py-1.5 text-sm font-medium text-orange-800 hover:bg-orange-50 dark:border-orange-400 dark:text-orange-300 dark:hover:bg-orange-950"
    >
      New chat
    </button>
  );

  const list =
    chats.length === 0 ? (
      <p className="text-xs text-zinc-600 dark:text-zinc-400">Your chats appear here (kept in this browser).</p>
    ) : (
      <ol className="space-y-1">
        {chats.map((c) => {
          const title = shorten(c.title || c.conversation_id, 80);
          const active = c.conversation_id === current;
          const when = formatRelative(c.updated_at, now);
          return (
            <li key={c.conversation_id} className="space-y-1">
              <div className="flex items-start gap-1">
                <a
                  href={`/?chat=${encodeURIComponent(c.conversation_id)}`}
                  aria-current={active ? "page" : undefined}
                  onClick={(e) => {
                    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return; // new tab
                    e.preventDefault();
                    onOpen(c.conversation_id);
                  }}
                  className={`min-w-0 flex-1 rounded px-1.5 py-1 text-sm break-words hover:bg-zinc-100 dark:hover:bg-zinc-800 ${
                    active ? "border-l-4 border-orange-700 bg-orange-50 font-medium dark:border-orange-400 dark:bg-orange-950" : ""
                  }`}
                >
                  {title}
                  <span className="block text-xs font-normal text-zinc-600 dark:text-zinc-400">
                    {plural(c.turns.length, "turn")}
                    {when && ` · ${when}`}
                    {c.read_only && " · read-only"}
                  </span>
                </a>
                <button
                  type="button"
                  onClick={() => setConfirming(c.conversation_id)}
                  aria-label={`Remove chat: ${shorten(c.title || c.conversation_id, 60)}`}
                  className="rounded px-1.5 text-sm text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
                >
                  ×
                </button>
              </div>
              {confirming === c.conversation_id && (
                <div
                  role="group"
                  aria-label={`Confirm removing ${shorten(c.title || c.conversation_id, 60)}`}
                  className="space-y-1 rounded border border-zinc-300 p-2 text-xs dark:border-zinc-700"
                >
                  <p>Remove this chat from this browser? Anyone with its link can still open it.</p>
                  <div className="flex gap-3">
                    <button
                      type="button"
                      onClick={() => {
                        setConfirming(null);
                        onRemove(c.conversation_id);
                      }}
                      className="font-medium text-red-800 underline dark:text-red-300"
                    >
                      Remove
                    </button>
                    <button type="button" onClick={() => setConfirming(null)} className="underline">
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ol>
    );

  return (
    <nav aria-label="Chats" className="min-w-0">
      {wide ? (
        <div className="space-y-2">
          {newButton}
          <h2 className="text-sm font-medium">Chats ({chats.length})</h2>
          {list}
        </div>
      ) : (
        <details className="rounded-md border border-zinc-300 p-2 dark:border-zinc-700">
          <summary className="cursor-pointer text-sm font-medium">Chats ({chats.length})</summary>
          <div className="mt-2 space-y-2">
            {newButton}
            {list}
          </div>
        </details>
      )}
    </nav>
  );
}
