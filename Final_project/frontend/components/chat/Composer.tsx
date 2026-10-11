"use client";
import { forwardRef } from "react";

interface Props {
  value: string;
  maxChars: number;
  disabledReason: string | null;
  busy: boolean;
  placeholder?: string;
  note?: string | null; // e.g. "This chat is read-only: your question starts a new chat."
  onChange: (value: string) => void;
  onSubmit: () => void;
  children?: React.ReactNode; // run settings, under the box
}

/** From Lab 4: Enter sends, Shift+Enter adds a line; the disabled reason is visible text. */
const Composer = forwardRef<HTMLTextAreaElement, Props>(function Composer(p, ref) {
  const disabled = p.disabledReason !== null;
  return (
    <form
      aria-label="Ask a question"
      onSubmit={(e) => {
        e.preventDefault();
        if (!disabled) p.onSubmit();
      }}
      className="space-y-3 rounded-lg border border-zinc-300 bg-[var(--background)] p-3 dark:border-zinc-700"
    >
      <label htmlFor="question" className="text-sm font-medium">
        Research question
      </label>
      <textarea
        id="question"
        ref={ref}
        rows={2}
        value={p.value}
        maxLength={p.maxChars}
        onChange={(e) => p.onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (!disabled) p.onSubmit();
          }
        }}
        placeholder={p.placeholder ?? "e.g. When should I use RAG instead of fine-tuning?"}
        className="w-full resize-y rounded-md border border-zinc-400 bg-white p-2 text-sm dark:border-zinc-600 dark:bg-zinc-900"
        aria-describedby="composer-hint"
      />
      {p.note && <p className="text-xs text-zinc-700 dark:text-zinc-300">{p.note}</p>}
      {p.children}
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={disabled}
          aria-busy={p.busy}
          className="rounded-md bg-orange-700 px-4 py-1.5 text-sm font-medium text-white hover:bg-orange-800 disabled:bg-zinc-600 dark:disabled:bg-zinc-700"
        >
          Research
        </button>
        <span id="composer-hint" className="text-xs text-zinc-600 dark:text-zinc-400">
          {p.disabledReason ?? "Enter to send, Shift+Enter for a new line."} {p.value.length}/{p.maxChars}
        </span>
      </div>
    </form>
  );
});

export default Composer;
