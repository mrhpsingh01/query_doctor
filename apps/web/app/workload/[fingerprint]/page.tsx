"use client";

import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { QueryDetailView } from "@/components/workload/QueryDetailView";
import { api } from "@/lib/api";
import type { WorkloadQueryDetail } from "@/lib/types";
import { useDefaultProject } from "@/lib/useDefaultProject";

function WorkloadQueryPageContent() {
  const fingerprint = useParams<{ fingerprint: string }>().fingerprint;
  const rawConnection = useSearchParams().get("connection_id");
  const connectionId = rawConnection === null ? undefined : Number(rawConnection);
  const { project, error: projectError } = useDefaultProject();
  const [detail, setDetail] = useState<WorkloadQueryDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (project) api.getWorkloadQuery(project.id, fingerprint, connectionId !== undefined && Number.isInteger(connectionId) && connectionId >= 0 ? connectionId : undefined).then(setDetail).catch((e: Error) => setError(e.message));
  }, [project, fingerprint, connectionId]);

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

export default function WorkloadQueryPage() {
  return <Suspense fallback={<p className="text-sm text-ink-2">Loading…</p>}><WorkloadQueryPageContent /></Suspense>;
}
