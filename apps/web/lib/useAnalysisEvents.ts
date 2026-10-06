"use client";

import { useEffect, useState } from "react";
import type { ProgressEvent } from "./types";

export const STAGES = [
  "validating_query",
  "collecting_schema",
  "generating_plan",
  "diagnosing",
  "testing_candidates",
] as const;
const TERMINAL = new Set(["completed", "failed", "cancelled", "benchmark_completed"]);

export interface ProgressState {
  /** Every event seen so far, in order. */
  events: ProgressEvent[];
  /** The latest stage reached (or the terminal event type). */
  current: string | null;
  done: boolean;
}

const EMPTY: ProgressState = { events: [], current: null, done: false };

/** Follows an analysis's server-sent progress events and stops after a terminal one. */
export function useAnalysisEvents(analysisId: number | null, url?: string): ProgressState {
  // State is tagged with the stream it belongs to, so switching analyses starts from empty without a reset effect.
  const streamKey = analysisId === null ? null : (url ?? `/api/analyses/${analysisId}/events`);
  const [tagged, setTagged] = useState<{ key: string | null; value: ProgressState }>({ key: null, value: EMPTY });
  const state = tagged.key === streamKey ? tagged.value : EMPTY;

  useEffect(() => {
    if (streamKey === null || typeof EventSource === "undefined") return;
    const source = new EventSource(streamKey);

    source.addEventListener("progress", (message) => {
      let event: ProgressEvent;
      try {
        event = JSON.parse((message as MessageEvent).data);
      } catch {
        return;
      }
      const done = TERMINAL.has(event.type);
      setTagged((prev) => {
        const base = prev.key === streamKey ? prev.value : EMPTY;
        return { key: streamKey, value: { events: [...base.events, event], current: event.type, done } };
      });
      if (done) source.close();
    });
    return () => source.close();
  }, [streamKey]);

  return state;
}
