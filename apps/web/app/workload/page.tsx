"use client";

import { RecordedWorkload } from "@/components/workload/RecordedWorkload";
import { WorkloadDashboard } from "@/components/workload/WorkloadDashboard";
import { SHOWCASE_ONLY } from "@/lib/deployment";
import { useDefaultProject } from "@/lib/useDefaultProject";

export default function WorkloadPage() {
  return SHOWCASE_ONLY ? <RecordedWorkload /> : <LiveWorkload />;
}

function LiveWorkload() {
  const { project, error } = useDefaultProject();
  if (error) {
    return (
      <p role="alert" className="rounded-md border border-heat-3 bg-heat-3-soft px-3 py-2 text-sm">
        {error}
      </p>
    );
  }
  if (!project) return <p className="text-sm text-ink-2">Loading…</p>;
  return <WorkloadDashboard projectId={project.id} />;
}
