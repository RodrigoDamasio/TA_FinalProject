"use client";
import { useState } from "react";
import Transcript from "@/components/transcript/Transcript";
import type { ResearchView } from "@/lib/schemas";
import Deliveries from "./Deliveries";
import MetaBar from "./MetaBar";
import PerAgentTable from "./PerAgentTable";

/** Collapsible run details under the report: metadata, per-agent usage, deliveries (runs
 * with a callback or Telegram channel) and the full transcript. Loaded when opened. */
export default function RunDetails({ run }: { run: ResearchView }) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="rounded-md border border-zinc-200 p-2 dark:border-zinc-800"
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
    >
      <summary className="cursor-pointer text-sm font-medium">Run details</summary>
      {open && (
        <div className="mt-3 space-y-4">
          <MetaBar run={run} />
          <section aria-label="Per agent" className="space-y-1">
            <h3 className="text-xs font-semibold tracking-wide uppercase">Per agent</h3>
            <PerAgentTable perAgent={run.metadata.per_agent} />
          </section>
          {run.channel !== "web" && !run.sample && (
            <section aria-label="Deliveries section" className="space-y-1">
              <h3 className="text-xs font-semibold tracking-wide uppercase">Deliveries</h3>
              <Deliveries runId={run.run_id} />
            </section>
          )}
          <section aria-label="Transcript section" className="space-y-1">
            <h3 className="text-xs font-semibold tracking-wide uppercase">Transcript</h3>
            <Transcript run={run} />
          </section>
        </div>
      )}
    </details>
  );
}
