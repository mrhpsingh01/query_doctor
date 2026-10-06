"use client";

import { useEffect, useState } from "react";
import { ModelsView } from "@/components/models/ModelsView";
import { api } from "@/lib/api";
import type { ModelMonitor, ModelResponse } from "@/lib/types";

export default function ModelsPage() {
  const [data, setData] = useState<{ model: ModelResponse; monitor: ModelMonitor } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.getModel(), api.getModelMonitor()])
      .then(([model, monitor]) => setData({ model, monitor }))
      .catch((e: Error) => setError(e.message));
  }, []);

  if (error) {
    return (
      <p role="alert" className="rounded-md border border-heat-3 bg-heat-3-soft px-3 py-2 text-sm">
        {error}
      </p>
    );
  }
  if (!data) return <p className="text-sm text-ink-2">Loading…</p>;
  return <ModelsView model={data.model} monitor={data.monitor} />;
}
