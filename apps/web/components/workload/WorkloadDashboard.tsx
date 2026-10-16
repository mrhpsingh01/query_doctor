"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DbConnection, Engine, WorkloadDashboard as Dashboard } from "@/lib/types";
import { FixQueueSummary } from "./FixQueueSummary";
import { Panels } from "./Panels";
import { QueryTable } from "./QueryTable";
import { RegressionList } from "./RegressionList";

const BUTTON = "rounded-md border px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-60";

const ENGINES: { value: Engine; label: string }[] = [
  { value: "postgres", label: "PostgreSQL" },
  { value: "mongodb", label: "MongoDB" },
];

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
  const [engine, setEngine] = useState<Engine>("postgres");
  const [connections, setConnections] = useState<DbConnection[]>([]);
  const [sourceId, setSourceId] = useState<number | null>(null);
  const [profilerFile, setProfilerFile] = useState<File | null>(null);
  const mongo = engine === "mongodb";
  const sources = connections.filter((c) => (c.engine ?? "postgres") === engine);

  const load = useCallback(() => {
    const matching = connections.some((c) => (c.engine ?? "postgres") === engine);
    return (matching
      ? api.getWorkload(projectId, undefined, undefined, engine, sourceId ?? 0)
      : api.getWorkload(projectId, undefined, undefined, engine)).then(setData);
  }, [projectId, engine, sourceId, connections]);

  useEffect(() => {
    api.listConnections(projectId).then((rows) => { if (rows.length > 0) setConnections(rows); }).catch(() => {});
  }, [projectId]);

  useEffect(() => {
    load().catch((e) => setError(messageOf(e)));
  }, [load]);

  function chooseEngine(next: Engine) {
    setEngine(next);
    setData(null); // never show one database's ranking under the other's name
    setError(null);
    setNotice(null);
    setSourceId(null);
    setProfilerFile(null);
  }

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
      if (mongo && sourceId !== null) {
        if (!profilerFile) throw new Error("Choose a profiler sample JSON file with capture_start, capture_end, coverage, engine_version, and entries.");
        let payload: Record<string, unknown>;
        try {
          payload = JSON.parse(await profilerFile.text()) as Record<string, unknown>;
        } catch {
          throw new Error("The profiler sample is not valid JSON.");
        }
        await api.importProfiler(projectId, { ...payload, connection_id: sourceId });
      } else if (sourceId !== null) {
        await api.importWorkload(projectId, engine, sourceId);
      } else {
        await api.importWorkload(projectId, engine);
      }
      await load();
    });
  const demo = (mode: "baseline" | "regress") =>
    run(
      () => api.runDemoWorkload(projectId, mode, engine),
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
          <fieldset className="mt-3 flex flex-wrap gap-2">
            <legend className="sr-only">Database</legend>
            {ENGINES.map((option) => (
              <label
                key={option.value}
                className={`flex cursor-pointer items-center gap-2 rounded-md border px-3 py-1.5 text-sm ${
                  engine === option.value ? "border-accent bg-accent-soft" : "border-line bg-surface"
                }`}
              >
                <input type="radio" name="workload-engine" checked={engine === option.value} onChange={() => chooseEngine(option.value)} />
                <span className="font-medium">{option.label}</span>
              </label>
            ))}
          </fieldset>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={importNow} disabled={busy} className={`${BUTTON} border-accent bg-accent text-accent-ink`}>
            {busy ? "Working…" : mongo ? "Import profiler data" : "Import statistics"}
          </button>
          {sourceId === null && <button type="button" onClick={() => demo("baseline")} disabled={busy} className={`${BUTTON} border-line bg-surface`}>
            Generate demo traffic
          </button>}
          {sourceId === null && <button type="button" onClick={() => demo("regress")} disabled={busy} className={`${BUTTON} border-line bg-surface`}>
            Simulate a regression
          </button>}
        </div>
      </div>

      {sources.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <label htmlFor="workload-source" className="font-medium">Source</label>
          <select id="workload-source" className="rounded-md border border-line bg-surface px-3 py-2" value={sourceId ?? ""}
            onChange={(event) => { setSourceId(event.target.value ? Number(event.target.value) : null); setProfilerFile(null); setData(null); }}>
            <option value="">Sample database</option>
            {sources.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          {mongo && sourceId !== null && (
            <label className="grid gap-1">
              Profiler sample JSON
              <input type="file" accept=".json,application/json" onChange={(event) => setProfilerFile(event.target.files?.[0] ?? null)} />
            </label>
          )}
        </div>
      )}

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

      <section aria-label="Ranked Fix Queue" className="rounded-md border border-line bg-surface p-4">
        <h2 className="text-lg font-semibold">Ranked Fix Queue</h2>
        <FixQueueSummary queue={data?.fix_queue} />
        {snapshot && !mongo && sourceId === null && snapshot.source_connection_id == null && <button type="button" disabled={busy} className={`${BUTTON} mt-3 border-accent`} onClick={() => run(async () => {
          const result = await api.runWorkloadBatch(projectId, snapshot.id);
          const skipped = result.skipped.length ? ` Skipped ${result.skipped.length}: ${[...new Set(result.skipped.map(item => item.reason))].join(" ")}` : "";
          setNotice(`Queued ${result.analysis_ids.length} analyses with isolated benchmarks.${skipped} Refresh after the worker finishes.`);
        })}>Analyze top 5 &amp; approve isolated benchmarks</button>}
        <button type="button" disabled={busy} className={`${BUTTON} ml-2 mt-3 border-line`} onClick={() => run(load)}>Refresh queue</button>
      </section>

      {data && !snapshot && (
        <section className="rounded-md border border-line bg-surface p-5">
          <h2 className="text-lg font-semibold">No workload imported yet</h2>
          <p className="mt-1 max-w-[62ch] text-sm text-ink-2">
            {mongo
              ? "Import the MongoDB profiler's record (system.profile) to rank its query shapes by the total time they cost. A fresh sample database has no traffic of its own: generate demo traffic first, then import."
              : "Import the database's pg_stat_statements statistics to rank its queries by the total time they cost. A fresh sample database has no traffic of its own: generate demo traffic first, then import."}
          </p>
        </section>
      )}

      {data && snapshot && (
        <>
          <p className="text-sm text-ink-2">
            Snapshot taken {when(snapshot.taken_at)}: {snapshot.query_count} queries, {snapshot.skipped_count} skipped (
            {mongo ? "not a find or an aggregate, or the database's own commands" : "not a single SELECT, or the database's own bookkeeping"}).
            {data.compare_to && <> Compared with the snapshot of {when(data.compare_to.taken_at)}.</>}
            {snapshot.coverage && <> Coverage: {snapshot.coverage.replaceAll("_", " ")}.</>}
          </p>
          {snapshot.warnings?.map((warning) => <p key={warning} className="text-xs text-ink-2">{warning}</p>)}
          <section aria-labelledby="ranked-h" className="grid gap-3">
            <h2 id="ranked-h" className="text-lg font-semibold">
              Ranked by total time
            </h2>
            <QueryTable queries={data.queries} engine={engine} sourceConnectionId={sourceId ?? (sources.length > 0 ? 0 : null)} />
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
