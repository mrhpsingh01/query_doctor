"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { AnalysisList } from "@/components/AnalysisList";
import { QueryEditor, type QuerySubmission } from "@/components/query-editor/QueryEditor";
import { ConnectionSelect } from "@/components/settings/ConnectionSelect";
import { ApiError, api } from "@/lib/api";
import type { AnalysisSummary, DbConnection, SampleQuery } from "@/lib/types";
import { useDefaultProject } from "@/lib/useDefaultProject";

function NewAnalysis() {
  const router = useRouter();
  const search = useSearchParams();
  const sampleId = search.get("sample");
  const linkedSql = search.get("sql") ?? undefined; // from the workload page
  const linkedParams = search.get("params") ?? undefined;
  const { project, error: projectError } = useDefaultProject();
  const [samples, setSamples] = useState<SampleQuery[] | null>(null);
  const [history, setHistory] = useState<AnalysisSummary[]>([]);
  const [connections, setConnections] = useState<DbConnection[]>([]);
  const [connectionId, setConnectionId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listSamples().then(setSamples).catch(() => setSamples([]));
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
      const { id } = await api.createAnalysis({ ...input, project_id: project.id, connection_id: connectionId });
      router.push(`/analyses/${id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong while starting the analysis. Try again.");
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-8">
      <h1 className="text-2xl font-semibold tracking-tight">New analysis</h1>
      <ConnectionSelect connections={connections} value={connectionId} onChange={setConnectionId} />
      {samples === null ? (
        <p className="text-sm text-ink-2">Loading…</p>
      ) : (
        <QueryEditor
          key={sampleId ?? linkedSql ?? "blank"}
          samples={samples}
          onSubmit={submit}
          busy={busy}
          error={error ?? projectError}
          initialSampleId={sampleId}
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

export default function Page() {
  return (
    <Suspense fallback={<p className="text-sm text-ink-2">Loading…</p>}>
      <NewAnalysis />
    </Suspense>
  );
}
