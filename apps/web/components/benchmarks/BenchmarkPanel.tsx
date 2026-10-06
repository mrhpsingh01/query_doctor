"use client";

import { useEffect, useRef } from "react";
import { formatBytes, formatMs } from "@/lib/format";
import type { BenchmarkRun } from "@/lib/types";
import { useAnalysisEvents } from "@/lib/useAnalysisEvents";
import { LatencyChart } from "./LatencyChart";
import { RunTable } from "./RunTable";

const STAGE_LABELS: Record<string, string> = {
  benchmarking: "Starting",
  creating_sandbox: "Cloning the database",
  warmup_before: "Warming up the original query",
  measuring_before: "Timing the original query",
  creating_index: "Building the index in the copy",
  analyzing: "Refreshing statistics",
  warmup_after: "Warming up with the index",
  measuring_after: "Timing the query with the index",
  comparing: "Comparing the results",
  teardown: "Removing the copy",
};

interface Props {
  run: BenchmarkRun;
  onCancel?: (run: BenchmarkRun) => void;
  /** Called once when a followed benchmark reaches a terminal state, so the page can reload the analysis. */
  onFinished?: () => void;
}

const trimmed = (n: number) => n.toFixed(1).replace(/\.0$/, "");
const isBlocking = (warning: string) => warning.startsWith("Output differs");

export function BenchmarkPanel({ run, onCancel, onFinished }: Props) {
  const active = run.status === "queued" || run.status === "running";
  const progress = useAnalysisEvents(active ? run.id : null, active ? `/api/benchmarks/${run.id}/events` : undefined);
  const fired = useRef(false);
  useEffect(() => {
    if (progress.done && !fired.current) {
      fired.current = true;
      onFinished?.();
    }
  }, [progress.done, onFinished]);

  if (run.status === "failed") {
    return (
      <p role="alert" className="rounded-md border border-heat-3 bg-heat-3-soft px-3 py-2 text-sm">
        {run.error ?? "The benchmark failed."}
      </p>
    );
  }
  if (run.status === "cancelled") return <p className="text-sm text-ink-2">This benchmark was cancelled.</p>;

  if (active) {
    const stage = progress.current ?? run.stage;
    return (
      <div className="grid gap-2 rounded-md border border-line bg-surface-2/50 p-3 text-sm" aria-live="polite">
        {run.status === "queued" && !stage ? (
          <p>Waiting for a free benchmark slot…</p>
        ) : (
          <p className="flex items-center gap-2">
            <span aria-hidden="true" className="inline-block h-2.5 w-2.5 animate-pulse rounded-full bg-accent" />
            {(stage && STAGE_LABELS[stage]) ?? "Working"}…
          </p>
        )}
        {onCancel && (
          <div>
            <button type="button" onClick={() => onCancel(run)} className="rounded border border-line px-2.5 py-1 text-xs font-medium hover:border-heat-3">
              Cancel benchmark
            </button>
          </div>
        )}
      </div>
    );
  }

  const result = run.result;
  if (!result) return null;
  const faster = result.improvement_pct >= 0;
  const blocking = result.warnings.some(isBlocking);

  return (
    <div className="grid gap-4 rounded-md border border-line bg-surface p-4">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span
          data-outcome={faster ? "faster" : "slower"}
          className={`text-2xl font-semibold ${faster ? "text-heat-0" : "text-heat-3"}`}
        >
          {trimmed(Math.abs(result.improvement_pct))}% {faster ? "faster" : "slower"}
        </span>
        <span className="text-sm text-ink-2">
          median {formatMs(result.before.median_ms)} → {formatMs(result.after.median_ms)}
        </span>
      </div>
      {!faster && (
        <p className="text-sm">The index made this query slower in the test, so it is not recommended for this query.</p>
      )}

      <table aria-label="Summary" className="w-full max-w-md text-left text-sm">
        <thead>
          <tr className="text-ink-2">
            <th className="py-1 pr-4 font-medium">Metric</th>
            <th className="py-1 pr-4 font-medium">Original</th>
            <th className="py-1 font-medium">With index</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          <tr className="border-t border-line">
            <th scope="row" className="py-1 pr-4 font-sans font-normal">Median</th>
            <td className="py-1 pr-4">{formatMs(result.before.median_ms)}</td>
            <td className="py-1">{formatMs(result.after.median_ms)}</td>
          </tr>
          <tr className="border-t border-line">
            <th scope="row" className="py-1 pr-4 font-sans font-normal">p95</th>
            <td className="py-1 pr-4">{formatMs(result.before.p95_ms)}</td>
            <td className="py-1">{formatMs(result.after.p95_ms)}</td>
          </tr>
          <tr className="border-t border-line">
            <th scope="row" className="py-1 pr-4 font-sans font-normal">Measured runs</th>
            <td className="py-1 pr-4">{result.before.n}</td>
            <td className="py-1">{result.after.n}</td>
          </tr>
        </tbody>
      </table>

      <LatencyChart result={result} />

      <div className="grid gap-0.5 text-sm">
        <p>Index size: {formatBytes(result.index_size_bytes)}</p>
        <p>Index build time: {formatMs(result.index_build_ms)}</p>
        {result.checksum_match && <p>Same output with and without the index.</p>}
        {result.plan_fingerprint_before !== result.plan_fingerprint_after && <p>The execution plan changed shape.</p>}
      </div>

      {result.warnings.length > 0 && (
        <div className="grid gap-1.5">
          <h4 className="text-sm font-semibold">Reliability warnings</h4>
          <ul aria-label="Reliability warnings" className="grid gap-1.5">
            {result.warnings.map((w) => (
              <li
                key={w}
                data-blocking={isBlocking(w)}
                className={`rounded border px-2.5 py-1.5 text-sm ${isBlocking(w) ? "border-heat-3 bg-heat-3-soft" : "border-heat-1 bg-heat-1-soft"}`}
              >
                {w}
              </li>
            ))}
          </ul>
          {blocking && <p className="text-sm text-ink-2">Because the output differed, this index was not validated.</p>}
        </div>
      )}

      <RunTable result={result} />

      <p className="text-xs text-ink-2">
        Measured in an isolated copy of the database ({result.pg_version}, dataset {result.dataset_snapshot}); the database
        being analyzed was never changed. Timings include a small, equal instrumentation overhead on both sides.
      </p>
    </div>
  );
}
