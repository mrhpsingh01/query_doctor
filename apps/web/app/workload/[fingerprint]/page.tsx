"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { QueryDetailView } from "@/components/workload/QueryDetailView";
import { api } from "@/lib/api";
import type { WorkloadQueryDetail } from "@/lib/types";
import { useDefaultProject } from "@/lib/useDefaultProject";

export default function WorkloadQueryPage() {
  const fingerprint = useParams<{ fingerprint: string }>().fingerprint;
  const { project, error: projectError } = useDefaultProject();
  const [detail, setDetail] = useState<WorkloadQueryDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (project) api.getWorkloadQuery(project.id, fingerprint).then(setDetail).catch((e: Error) => setError(e.message));
  }, [project, fingerprint]);

  const problem = error ?? projectError;
  if (problem) {
    return (
      <p role="alert" className="rounded-md border border-heat-3 bg-heat-3-soft px-3 py-2 text-sm">
        {problem}
      </p>
    );
  }
  if (!detail) return <p className="text-sm text-ink-2">Loading…</p>;
  return <QueryDetailView detail={detail} />;
}
