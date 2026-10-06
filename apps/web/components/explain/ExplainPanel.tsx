"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { Explanation, ExplanationKind } from "@/lib/types";

const KINDS: { kind: ExplanationKind; label: string }[] = [
  { kind: "plan", label: "Explain the plan" },
  { kind: "findings", label: "Summarise the findings" },
  { kind: "tradeoffs", label: "Trade-offs" },
  { kind: "next_experiment", label: "Next experiment" },
  { kind: "pr_comment", label: "Pull-request comment" },
];
const BUTTON = "rounded-md border border-line bg-surface px-3 py-1.5 text-sm font-medium hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-60";
const GIVE_UP_AFTER = 120;

function sourceLabel(source: string | null): string {
  if (!source) return "";
  if (source === "template") return "Template";
  return `Ollama · ${source.replace(/^ollama:/, "")}, validated`;
}

/** Plain-language explanations of an analysis, each built only from numbered facts that are shown alongside it. */
export function ExplainPanel({ analysisId, pollMs = 1000 }: { analysisId: number; pollMs?: number }) {
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [decision, setDecision] = useState<boolean | null>(null);
  const run = useRef(0);

  useEffect(() => () => void (run.current += 1), []);

  const ask = useCallback(
    async (kind: ExplanationKind) => {
      const mine = ++run.current;
      setError(null);
      setExplanation(null);
      setCopied(false);
      setDecision(null);
      setPending(true);
      try {
        const { id } = await api.createExplanation(analysisId, kind);
        for (let attempt = 0; attempt < GIVE_UP_AFTER; attempt += 1) {
          const current = await api.getExplanation(id);
          if (mine !== run.current) return;
          if (current.status !== "queued") {
            setExplanation(current);
            if (current.status === "failed") setError(current.error ?? "The explanation could not be produced. Try again.");
            return;
          }
          await new Promise((resolve) => setTimeout(resolve, pollMs));
        }
        if (mine === run.current) setError("The explanation is taking too long. Try again in a moment.");
      } catch (e) {
        if (mine === run.current) setError(e instanceof ApiError ? e.message : "The explanation could not be requested. Try again.");
      } finally {
        if (mine === run.current) setPending(false);
      }
    },
    [analysisId, pollMs],
  );

  async function review(accepted: boolean) {
    if (!explanation) return;
    try {
      await api.reviewExplanation(explanation.id, accepted);
      setDecision(accepted);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Your decision could not be saved.");
    }
  }

  async function copy() {
    if (!explanation?.text) return;
    try {
      await navigator.clipboard.writeText(explanation.text);
      setCopied(true);
    } catch {
      setError("The comment could not be copied. Select the text and copy it instead.");
    }
  }

  const ready = explanation && explanation.status === "completed" ? explanation : null;
  return (
    <section aria-labelledby="explain-h" className="grid gap-3">
      <h2 id="explain-h" className="text-lg font-semibold">
        Explain
      </h2>
      <p className="max-w-[70ch] text-sm text-ink-2">
        An explanation states only what the measurements and plan show, and every sentence points to the facts it used. Nothing it
        says is ever run.
      </p>
      <div className="flex flex-wrap gap-2">
        {KINDS.map(({ kind, label }) => (
          <button key={kind} type="button" className={BUTTON} disabled={pending} onClick={() => ask(kind)}>
            {label}
          </button>
        ))}
      </div>

      {pending && (
        <p role="status" className="text-sm text-ink-2">
          Writing the explanation…
        </p>
      )}
      {error && (
        <p role="alert" className="rounded-md border border-heat-3 bg-heat-3-soft px-3 py-2 text-sm">
          {error}
        </p>
      )}

      {ready && (
        <article className="grid gap-3 rounded-md border border-line bg-surface p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="rounded border border-line px-2 py-0.5 text-xs font-semibold text-ink-2">{sourceLabel(ready.source)}</span>
            {ready.kind === "pr_comment" && (
              <button type="button" className={BUTTON} onClick={copy}>
                Copy comment
              </button>
            )}
          </div>
          <p className="max-w-[75ch] whitespace-pre-wrap text-[15px] leading-relaxed">{ready.text}</p>
          {copied && <p role="status" className="text-sm text-ink-2">Copied to the clipboard.</p>}
          {ready.fallback_reason && <p className="rounded-md border border-line bg-surface-2 px-3 py-2 text-sm">{ready.fallback_reason}</p>}
          {ready.dropped_sentences.length > 0 && (
            <details className="text-sm">
              <summary className="cursor-pointer text-ink-2">
                {ready.dropped_sentences.length} {ready.dropped_sentences.length === 1 ? "sentence was" : "sentences were"} removed because the evidence did not support {ready.dropped_sentences.length === 1 ? "it" : "them"}
              </summary>
              <ul className="mt-2 grid gap-1 text-ink-2">
                {ready.dropped_sentences.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            </details>
          )}
          <details className="text-sm">
            <summary className="cursor-pointer text-ink-2">Facts used ({ready.facts.length})</summary>
            <ul className="mt-2 grid gap-1">
              {ready.facts.map((f) => (
                <li key={f.id}>
                  <span className="mr-2 font-mono text-xs text-ink-2">{f.id}</span>
                  <span className="font-medium">{f.label}</span>: {f.text}
                </li>
              ))}
            </ul>
          </details>
          <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3 text-sm">
            {decision === null ? (
              <>
                <span className="text-ink-2">Does this explanation match what you see?</span>
                <button type="button" className={BUTTON} onClick={() => review(true)}>
                  Accept
                </button>
                <button type="button" className={BUTTON} onClick={() => review(false)}>
                  Reject
                </button>
              </>
            ) : (
              <span className="text-ink-2">You {decision ? "accepted" : "rejected"} this explanation.</span>
            )}
          </div>
        </article>
      )}
    </section>
  );
}
