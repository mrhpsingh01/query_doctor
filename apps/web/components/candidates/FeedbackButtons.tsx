"use client";

import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import { sessionId } from "@/lib/session";

const BUTTON = "rounded-md border border-line bg-surface px-2.5 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-60";

/** A thumbs up or down on an index idea; one vote per browser session (voting again would change it, so it locks). */
export function FeedbackButtons({ analysisId, candidateId }: { analysisId: number; candidateId: number }) {
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function vote(helpful: boolean) {
    setBusy(true);
    setError(null);
    try {
      await api.sendFeedback(analysisId, candidateId, helpful, sessionId());
      setSent(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The feedback could not be sent. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-ink-2">Was this idea useful?</span>
      <button type="button" className={BUTTON} disabled={busy || sent} onClick={() => vote(true)}>
        Helpful
      </button>
      <button type="button" className={BUTTON} disabled={busy || sent} onClick={() => vote(false)}>
        Not helpful
      </button>
      {sent && (
        <span role="status" className="text-ink-2">
          Thanks for the feedback.
        </span>
      )}
      {error && (
        <span role="alert" className="text-heat-3">
          {error}
        </span>
      )}
    </div>
  );
}
