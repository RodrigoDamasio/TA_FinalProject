"use client";
import { useEffect, useState } from "react";
import Badge, { type Tone } from "@/components/Badge";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { Delivery } from "@/lib/schemas";

const TONE: Record<string, Tone> = { delivered: "good", pending: "neutral", retrying: "warn", dead: "bad", failed: "bad" };

/** The run's outbox messages (callback / Telegram): no payload, no secret, host only. */
export default function Deliveries({ runId }: { runId: string }) {
  const [rows, setRows] = useState<Delivery[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.deliveries(runId).then(
      (d) => !cancelled && setRows(d),
      (err) => !cancelled && setError(err instanceof ApiError ? err.message : "Could not load the deliveries."),
    );
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (error) return <p className="text-xs text-red-800 dark:text-red-300">{error}</p>;
  if (!rows) return <p className="text-xs text-zinc-600 dark:text-zinc-400">Loading deliveries…</p>;
  if (rows.length === 0) return <p className="text-xs text-zinc-600 dark:text-zinc-400">No deliveries for this run.</p>;
  return (
    <div role="region" aria-label="Deliveries" tabIndex={0} className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <caption className="sr-only">Notifications sent for this run</caption>
        <thead>
          <tr className="border-b border-zinc-200 dark:border-zinc-800">
            {["Event", "Target", "Status", "Attempts", "Last result", "Next attempt"].map((h) => (
              <th key={h} scope="col" className="py-1 pr-3 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.id} className="border-b border-zinc-100 align-top dark:border-zinc-900">
              <td className="py-1 pr-3">{d.event}</td>
              <td className="py-1 pr-3">
                {d.kind} · {d.target_host ?? "—"}
              </td>
              <td className="py-1 pr-3">
                <Badge tone={TONE[d.status] ?? "neutral"}>{d.status}</Badge>
              </td>
              <td className="py-1 pr-3">{d.attempts}</td>
              <td className="py-1 pr-3">
                {d.last_status !== null ? `HTTP ${d.last_status}` : ""}
                {d.last_error ? ` ${d.last_error}` : ""}
                {d.last_status === null && !d.last_error ? "—" : ""}
              </td>
              <td className="py-1">{d.delivered_at ? `delivered ${formatDate(d.delivered_at)}` : formatDate(d.next_attempt_at) || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
