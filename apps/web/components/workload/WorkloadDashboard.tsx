"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { WorkloadDashboard as Dashboard } from "@/lib/types";
import { Panels } from "./Panels";
import { QueryTable } from "./QueryTable";
import { RegressionList } from "./RegressionList";

const BUTTON = "rounded-md border px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-60";

function when(iso: string) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong. Try again.";
}

/** Which query to optimize first: the imported pg_stat_statements snapshot, ranked, with panels and regressions. */
export function WorkloadDashboard({ projectId }: { projectId: number }) {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api.getWorkload(projectId).then(setData), [projectId]);

  useEffect(() => {
    load().catch((e) => setError(messageOf(e)));
  }, [load]);

  async function run(action: () => Promise<unknown>, after?: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      if (after) setNotice(after);
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  const importNow = () =>
    run(async () => {
      await api.importWorkload(projectId);
      await load();
    });
  const demo = (mode: "baseline" | "regress") =>
    run(
      () => api.runDemoWorkload(projectId, mode),
      "Demo traffic is running and takes about 30 seconds. Import again when it has finished.",
    );

  const snapshot = data?.snapshot ?? null;
  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Workload</h1>
          <p className="mt-1 max-w-[60ch] text-sm text-ink-2">
            Which queries cost the most database time, from the database&apos;s own statistics. Fix the top of the list first.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={importNow} disabled={busy} className={`${BUTTON} border-accent bg-accent text-accent-ink`}>
            {busy ? "Working…" : "Import statistics"}
          </button>
          <button type="button" onClick={() => demo("baseline")} disabled={busy} className={`${BUTTON} border-line bg-surface`}>
            Generate demo traffic
          </button>
          <button type="button" onClick={() => demo("regress")} disabled={busy} className={`${BUTTON} border-line bg-surface`}>
            Simulate a regression
          </button>
        </div>
      </div>

      {error && (
        <p role="alert" className="rounded-md border border-heat-3 bg-heat-3-soft px-3 py-2 text-sm">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="rounded-md border border-accent bg-accent-soft px-3 py-2 text-sm">
          {notice}
        </p>
      )}

      {data && !snapshot && (
        <section className="rounded-md border border-line bg-surface p-5">
          <h2 className="text-lg font-semibold">No workload imported yet</h2>
          <p className="mt-1 max-w-[62ch] text-sm text-ink-2">
            Import the database&apos;s pg_stat_statements statistics to rank its queries by the total time they cost. A fresh sample
            database has no traffic of its own: generate demo traffic first, then import.
          </p>
        </section>
      )}

      {data && snapshot && (
        <>
          <p className="text-sm text-ink-2">
            Snapshot taken {when(snapshot.taken_at)}: {snapshot.query_count} queries, {snapshot.skipped_count} skipped (not a single
            SELECT, or the database&apos;s own bookkeeping).
            {data.compare_to && <> Compared with the snapshot of {when(data.compare_to.taken_at)}.</>}
          </p>
          <section aria-labelledby="ranked-h" className="grid gap-3">
            <h2 id="ranked-h" className="text-lg font-semibold">
              Ranked by total time
            </h2>
            <QueryTable queries={data.queries} />
          </section>
          <section aria-labelledby="panels-h" className="grid gap-3">
            <h2 id="panels-h" className="text-lg font-semibold">
              Panels
            </h2>
            <Panels panels={data.panels} queries={data.queries} />
          </section>
          <section aria-labelledby="regressions-h" className="grid gap-3">
            <h2 id="regressions-h" className="text-lg font-semibold">
              Regressions
            </h2>
            <RegressionList regressions={data.regressions} queries={data.queries} comparing={data.compare_to !== null} />
          </section>
        </>
      )}
    </div>
  );
}
