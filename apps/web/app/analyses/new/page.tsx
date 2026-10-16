"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { AnalysisList } from "@/components/AnalysisList";
import { SHOWCASE_ONLY } from "@/lib/deployment";
import { OfflineAnalyzer } from "@/components/OfflineAnalyzer";
import { QueryEditor, type QuerySubmission } from "@/components/query-editor/QueryEditor";
import { ConnectionSelect } from "@/components/settings/ConnectionSelect";
import { ApiError, api } from "@/lib/api";
import type { AnalysisSummary, DbConnection, Engine, SampleQuery } from "@/lib/types";
import { useDefaultProject } from "@/lib/useDefaultProject";

function NewAnalysis() {
  const router = useRouter();
  const search = useSearchParams();
  const sampleId = search.get("sample");
  const linkedSql = search.get("sql") ?? undefined; // from the workload page
  const linkedParams = search.get("params") ?? undefined;
  const linkedEngine: Engine = search.get("engine") === "mongodb" ? "mongodb" : "postgres";
  const { project, error: projectError } = useDefaultProject();
  const [samples, setSamples] = useState<SampleQuery[] | null>(null);
  const [history, setHistory] = useState<AnalysisSummary[]>([]);
  const [connections, setConnections] = useState<DbConnection[]>([]);
  const linkedConnectionId = Number(search.get("connection_id"));
  const [connectionId, setConnectionId] = useState<number | null>(Number.isInteger(linkedConnectionId) && linkedConnectionId > 0 ? linkedConnectionId : null);
  const [engine, setEngine] = useState<Engine | null>(null); // null until the editor reports a choice: the linked one applies
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A linked MongoDB sample opens MongoDB even without ?engine=.
  const startEngine: Engine = samples?.find((x) => x.id === sampleId)?.engine ?? linkedEngine;

  useEffect(() => {
    // Both engines' samples; a failure to load one list still leaves the other usable.
    Promise.all([api.listSamples().catch(() => []), api.listSamples("mongodb").catch(() => [])]).then(([pg, mongo]) => setSamples([...pg, ...mongo]));
  }, []);
  useEffect(() => {
    if (!project) return;
    api.listProjectAnalyses(project.id).then((a) => setHistory(a.slice(0, 8))).catch(() => {});
    api.listConnections(project.id).then(setConnections).catch(() => setConnections([])); // off, or none yet: no selector
  }, [project]);

  async function submit(input: QuerySubmission) {
    if (!project) return;
    setBusy(true);
    setError(null);
    try {
      const selected = connections.find((c) => c.id === connectionId && (c.engine ?? "postgres") === input.engine);
      const { id } = await api.createAnalysis({ ...input, project_id: project.id, connection_id: selected?.id ?? null });
      router.push(`/analyses/${id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong while starting the analysis. Try again.");
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-8">
      <h1 className="text-2xl font-semibold tracking-tight">New analysis</h1>
      <ConnectionSelect connections={connections} value={connectionId} onChange={setConnectionId} engine={engine ?? startEngine} />
      {samples === null ? (
        <p className="text-sm text-ink-2">Loading…</p>
      ) : (
        <QueryEditor
          key={sampleId ?? linkedSql ?? startEngine}
          samples={samples}
          onSubmit={submit}
          busy={busy}
          error={error ?? projectError}
          initialSampleId={sampleId}
          initialEngine={startEngine}
          onEngineChange={(next) => { setEngine(next); setConnectionId(null); }}
          initialSql={linkedSql}
          initialParams={linkedParams}
        />
      )}
      <section aria-labelledby="history-h">
        <h2 id="history-h" className="mb-3 text-lg font-semibold">
          Previous analyses
        </h2>
        <AnalysisList analyses={history} empty="Nothing here yet. Your analyses will appear after you run one." />
      </section>
    </div>
  );
}

function AnalysisModes() {
  const search = useSearchParams();
  const [mode, setMode] = useState<"live" | "paste">(!SHOWCASE_ONLY && (search.get("mode") === "live" || search.has("sample") || search.has("sql")) ? "live" : "paste");
  if (SHOWCASE_ONLY) return <OfflineAnalyzer pasteOnly />;
  return <div className="grid gap-6">
    <div role="tablist" aria-label="Analysis mode" className="flex gap-4">
      {(["live", "paste"] as const).map(value => <button key={value} id={`tab-${value}`} role="tab" tabIndex={mode === value ? 0 : -1} aria-selected={mode === value} aria-controls={`panel-${value}`} className="rounded-md border border-line px-4 py-2 aria-selected:border-accent" onClick={() => setMode(value)} onKeyDown={event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? "live" : event.key === "End" ? "paste" : value === "live" ? "paste" : "live";
        setMode(next);
        document.getElementById(`tab-${next}`)?.focus();
      }}>{value === "live" ? "Live query" : "Paste EXPLAIN Output"}</button>)}
    </div>
    <div role="tabpanel" id={`panel-${mode}`} aria-labelledby={`tab-${mode}`}>{mode === "live" ? <NewAnalysis /> : <OfflineAnalyzer pasteOnly />}</div>
  </div>;
}

export default function Page() {
  return (
    <Suspense fallback={<p className="text-sm text-ink-2">Loading…</p>}>
      <AnalysisModes />
    </Suspense>
  );
}
