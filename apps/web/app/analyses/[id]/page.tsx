"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AnalysisResult } from "@/components/AnalysisResult";
import { ProgressStepper } from "@/components/ProgressStepper";
import { api } from "@/lib/api";
import type { Analysis } from "@/lib/types";
import { STAGES, useAnalysisEvents } from "@/lib/useAnalysisEvents";

export default function AnalysisPage() {
  const id = Number(useParams<{ id: string }>().id);
  const progress = useAnalysisEvents(Number.isFinite(id) ? id : null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Load once on arrival (finished analyses have nothing more to stream) and again when the stream ends.
  useEffect(() => {
    if (!Number.isFinite(id)) return;
    api.getAnalysis(id).then(setAnalysis).catch((e: Error) => setLoadError(e.message));
  }, [id, progress.done]);

  if (loadError) {
    return (
      <p role="alert" className="rounded-md border border-heat-3 bg-heat-3-soft px-3 py-2 text-sm">
        {loadError}
      </p>
    );
  }
  if (!analysis) return <p className="text-sm text-ink-2">Loading analysis…</p>;

  const finished = ["completed", "failed", "cancelled"].includes(analysis.status);
  const lastStage = [...progress.events].reverse().find((e) => (STAGES as readonly string[]).includes(e.type))?.type;
  const failure = progress.events.find((e) => e.type === "failed")?.message ?? analysis.error;

  return (
    <div className="grid gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Analysis {analysis.id}</h1>
        <p className="mt-1 text-sm text-ink-2">
          {analysis.execute ? "Measured by running the query" : "Planner estimates only (the query was not run)"}
        </p>
      </header>

      <pre className="overflow-x-auto rounded-md border border-line bg-surface p-3 font-mono text-[13px]">
        {analysis.sql}
      </pre>

      {analysis.status === "completed" ? (
        <AnalysisResult analysis={analysis} />
      ) : (
        <ProgressStepper
          current={finished ? analysis.status : progress.current}
          lastStage={lastStage ?? analysis.stage}
          message={failure}
        />
      )}
    </div>
  );
}
