"use client";

import { useEffect, useState } from "react";
import { AnalysisList } from "@/components/AnalysisList";
import { api } from "@/lib/api";
import type { AnalysisSummary } from "@/lib/types";
import { useDefaultProject } from "@/lib/useDefaultProject";

export default function History() {
  const { project, error } = useDefaultProject();
  const [analyses, setAnalyses] = useState<AnalysisSummary[] | null>(null);

  useEffect(() => {
    if (project) api.listProjectAnalyses(project.id).then(setAnalyses).catch(() => setAnalyses([]));
  }, [project]);

  return (
    <div className="grid gap-4">
      <h1 className="text-2xl font-semibold tracking-tight">History{project ? `: ${project.name}` : ""}</h1>
      {error && (
        <p role="alert" className="rounded-md border border-heat-3 bg-heat-3-soft px-3 py-2 text-sm">
          {error}
        </p>
      )}
      {analyses && <AnalysisList analyses={analyses} empty="No analyses yet. Run one from New analysis." />}
    </div>
  );
}
