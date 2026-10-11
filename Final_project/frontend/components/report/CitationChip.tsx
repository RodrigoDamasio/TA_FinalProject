"use client";
import { useEffect, useId, useRef, useState } from "react";
import { referenceAnchor, sourceLocation } from "@/lib/report";
import type { ReportSource } from "@/lib/schemas";
import { SOURCE_ICONS, SOURCE_LABELS, shorten } from "@/lib/format";

const WIDTH = 288; // px, w-72
const MARGIN = 8;

/** An [E3] citation: a link to the reference, with a popover (title, kind, location,
 * snippet) on hover and keyboard focus. The popover is placed in the viewport so it never
 * causes horizontal scrolling. All text is rendered as text. */
export default function CitationChip({ id, source, runId }: { id: string; source: ReportSource; runId: string }) {
  const [pos, setPos] = useState<{ left: number; top: number; above: boolean } | null>(null);
  const ref = useRef<HTMLAnchorElement>(null);
  const tipId = useId();

  function show() {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const vw = window.innerWidth || document.documentElement.clientWidth;
    const vh = window.innerHeight || document.documentElement.clientHeight;
    const width = Math.min(WIDTH, vw - 2 * MARGIN);
    const left = Math.max(MARGIN, Math.min(r.left, vw - width - MARGIN));
    const above = r.bottom + 180 > vh && r.top > 180;
    setPos({ left, top: above ? r.top - 4 : r.bottom + 4, above });
  }
  const hide = () => setPos(null);

  useEffect(() => {
    if (!pos) return;
    const close = () => setPos(null);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [pos]);

  const kind = source.source_kind;
  const location = sourceLocation(source);
  return (
    <span className="inline" onMouseEnter={show} onMouseLeave={hide}>
      <a
        ref={ref}
        href={`#${referenceAnchor(runId, id)}`}
        aria-label={`${id}: ${source.title}`}
        aria-describedby={pos ? tipId : undefined}
        data-citation={id}
        onFocus={show}
        onBlur={hide}
        onKeyDown={(e) => {
          if (e.key === "Escape") hide();
        }}
        className="mx-0.5 rounded bg-orange-50 px-1 font-mono text-xs font-semibold text-orange-800 no-underline hover:underline focus:outline-2 focus:outline-orange-700 dark:bg-orange-950 dark:text-orange-200"
      >
        {id}
      </a>
      {pos && (
        <span
          id={tipId}
          role="tooltip"
          style={{
            position: "fixed",
            left: pos.left,
            top: pos.top,
            width: `min(${WIDTH}px, calc(100vw - ${2 * MARGIN}px))`,
            transform: pos.above ? "translateY(-100%)" : undefined,
          }}
          className="z-30 block rounded-md border border-zinc-300 bg-white p-2 text-left text-xs leading-snug text-zinc-800 shadow-lg dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200"
        >
          <span className="block font-semibold">
            <span aria-hidden>{SOURCE_ICONS[kind] ?? "🔗"} </span>
            {source.title}
          </span>
          <span className="block text-zinc-600 dark:text-zinc-400">
            {SOURCE_LABELS[kind] ?? kind}
            {location ? ` · ${location}` : ""}
          </span>
          {source.snippet && <span className="mt-1 block">{shorten(source.snippet, 260)}</span>}
        </span>
      )}
    </span>
  );
}
