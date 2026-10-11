"use client";
import { useState } from "react";
import Badge from "@/components/Badge";
import { DEPTH_PROFILES, flagLabel, plural, SOURCE_ICONS, SOURCE_LABELS } from "@/lib/format";
import { orderedSources, referenceAnchor, reportToMarkdown, sourceLocation } from "@/lib/report";
import type { Depth, ResearchView } from "@/lib/schemas";
import { safeUrl } from "@/lib/citations";
import Markdown from "./Markdown";

const WARN_FLAGS = new Set(["forced", "degraded", "unsourced", "truncated", "canary_leak", "possible_injection"]);

export function Flags({ flags }: { flags: string[] }) {
  if (flags.length === 0) return null;
  return (
    <ul aria-label="Flags" className="flex flex-wrap gap-1.5">
      {flags.map((f) => (
        <li key={f}>
          <Badge tone={WARN_FLAGS.has(f) ? "warn" : "neutral"}>{flagLabel(f)}</Badge>
        </li>
      ))}
    </ul>
  );
}

function References({ run }: { run: ResearchView }) {
  const report = run.report!;
  const sources = orderedSources(report);
  if (sources.length === 0) return null;
  return (
    <section aria-label="References" className="border-t border-zinc-200 pt-3 dark:border-zinc-800">
      <h3 className="text-sm font-semibold">References</h3>
      <ol className="mt-2 space-y-2 text-sm">
        {sources.map((s) => {
          const href = s.url ? safeUrl(s.url) : "";
          const location = sourceLocation(s);
          return (
            <li key={s.id} id={referenceAnchor(run.run_id, s.id)} className="flex scroll-mt-20 gap-2">
              <span className="shrink-0 font-mono text-xs leading-5 font-semibold">{s.id}</span>
              <span className="min-w-0 break-words">
                <span aria-hidden>{SOURCE_ICONS[s.source_kind] ?? "🔗"} </span>
                <span className="sr-only">{SOURCE_LABELS[s.source_kind] ?? s.source_kind}: </span>
                {href ? (
                  <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-orange-800 underline dark:text-orange-300">
                    {s.title}
                  </a>
                ) : (
                  <span className="font-medium">{s.title}</span>
                )}
                {location && <span className="text-xs text-zinc-600 dark:text-zinc-400"> · {location}</span>}
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/** The structured report (from `report`, never re-parsed from final_output): title, lead,
 * sections with [E<n>] chips, references in order of first citation, and a footer. */
export default function Report({ run }: { run: ResearchView }) {
  const [copied, setCopied] = useState(false);
  const report = run.report;
  if (!report) return null;
  const m = run.metadata;
  const depth = DEPTH_PROFILES[report.depth as Depth];
  const markdown = reportToMarkdown(report);

  async function copy() {
    try {
      await navigator.clipboard.writeText(markdown);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  function download() {
    const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${run.run_id}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <article aria-label="Report" className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="accent">AI-generated</Badge>
        <Flags flags={run.flags} />
      </div>
      {(m.forced || m.degraded) && (
        <p className="rounded-md border border-amber-400 bg-amber-50 p-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
          {m.degraded
            ? "This report was assembled automatically from the findings (the Writer could not finish)."
            : "The Supervisor ran out of decisions: this report was completed without a final review."}
        </p>
      )}
      <header className="space-y-2">
        <h2 className="text-xl font-semibold">{report.title}</h2>
        <Markdown text={report.summary} sources={report.sources} runId={run.run_id} className="text-base font-medium" />
      </header>
      {report.sections.map((s, i) => (
        <section key={`${i}-${s.heading}`} className="space-y-2">
          <h3 className="text-lg font-semibold">{s.heading}</h3>
          <Markdown text={s.content} sources={report.sources} runId={run.run_id} />
        </section>
      ))}
      <References run={run} />
      <footer className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-zinc-200 pt-3 text-xs text-zinc-700 dark:border-zinc-800 dark:text-zinc-300">
        <span>
          Depth: {depth?.label ?? report.depth}
          {depth ? ` (${depth.words[0]}–${depth.words[1]} words)` : ""}
        </span>
        <span>{plural(report.word_count, "word")}</span>
        <span>
          Grounding:{" "}
          {m.grounding
            ? `${m.grounding.supported} of ${m.grounding.checked} claims supported`
            : "not fact-checked"}
        </span>
        <span className="flex gap-3">
          <button type="button" onClick={copy} className="font-medium text-orange-800 underline dark:text-orange-300">
            {copied ? "Copied" : "Copy as Markdown"}
          </button>
          <button type="button" onClick={download} className="font-medium text-orange-800 underline dark:text-orange-300">
            Download .md
          </button>
        </span>
      </footer>
    </article>
  );
}
