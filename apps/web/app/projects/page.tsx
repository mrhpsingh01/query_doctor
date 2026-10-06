"use client";

import { useEffect, useState } from "react";
import { AnalysisList } from "@/components/AnalysisList";
import { FingerprintTable } from "@/components/workload/FingerprintTable";
import { api } from "@/lib/api";
import type { AnalysisSummary, FingerprintHistory } from "@/lib/types";
import { useDefaultProject } from "@/lib/useDefaultProject";

export default function History() {
  const { project, error } = useDefaultProject();
  const [analyses, setAnalyses] = useState<AnalysisSummary[] | null>(null);
  const [fingerprints, setFingerprints] = useState<FingerprintHistory[] | null>(null);

  useEffect(() => {
    if (!project) return;
    api.listProjectAnalyses(project.id).then(setAnalyses).catch(() => setAnalyses([]));
    api.listFingerprints(project.id).then(setFingerprints).catch(() => setFingerprints([]));
  }, [project]);

  return (
    <div className="grid gap-4">
      <h1 className="text-2xl font-semibold tracking-tight">History{project ? `: ${project.name}` : ""}</h1>
      {error && (
        <p role="alert" className="rounded-md border border-heat-3 bg-heat-3-soft px-3 py-2 text-sm">
          {error}
        </p>
      )}
      {fingerprints && fingerprints.length > 0 && (
        <section aria-labelledby="by-query-h" className="grid gap-3">
          <h2 id="by-query-h" className="text-lg font-semibold">
            By query
          </h2>
          <FingerprintTable fingerprints={fingerprints} />
        </section>
      )}
      <section aria-labelledby="all-h" className="grid gap-3">
        <h2 id="all-h" className="text-lg font-semibold">
          All analyses
        </h2>
        {analyses && <AnalysisList analyses={analyses} empty="No analyses yet. Run one from New analysis." />}
      </section>
    </div>
  );
}
