"use client";

import { useState } from "react";
import { ApiError, api } from "@/lib/api";

const BUTTON = "rounded-md border px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-60";

/** Permanently deletes an analysis behind an explicit confirmation. */
export function DeleteAnalysisButton({ analysisId, onDeleted }: { analysisId: number; onDeleted: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await api.deleteAnalysis(analysisId);
      onDeleted();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The analysis could not be deleted. Try again.");
      setBusy(false);
    }
  }

  if (!confirming) {
    return (
      <button type="button" className={`${BUTTON} border-line bg-surface`} onClick={() => setConfirming(true)}>
        Delete this analysis
      </button>
    );
  }
  return (
    <div className="grid gap-2 rounded-md border border-heat-3 bg-heat-3-soft p-3 text-sm">
      <p>This permanently removes the analysis, its findings, index ideas, benchmarks, explanations and feedback.</p>
      <div className="flex gap-2">
        <button type="button" disabled={busy} className={`${BUTTON} border-heat-3 bg-heat-3 text-accent-ink`} onClick={remove}>
          Yes, delete
        </button>
        <button type="button" disabled={busy} className={`${BUTTON} border-line bg-surface`} onClick={() => setConfirming(false)}>
          Cancel
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
