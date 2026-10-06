import { STAGES } from "@/lib/useAnalysisEvents";

const LABELS: Record<(typeof STAGES)[number], string> = {
  validating_query: "Checking the query",
  collecting_schema: "Reading the schema",
  generating_plan: "Getting the execution plan",
  diagnosing: "Looking for problems",
  testing_candidates: "Testing index ideas",
};

interface Props {
  /** Latest event type: a stage name, or completed / failed / cancelled. */
  current: string | null;
  /** For failed / cancelled: the stage that was running when it stopped. */
  lastStage?: string | null;
  message?: string | null;
}

type StepState = "done" | "current" | "failed" | "waiting";

export function ProgressStepper({ current, lastStage, message }: Props) {
  const stopped = current === "failed" || current === "cancelled";
  const reached = stopped ? lastStage : current;
  const reachedIndex = reached ? STAGES.indexOf(reached as (typeof STAGES)[number]) : -1;

  function stateOf(index: number): StepState {
    if (current === "completed") return "done";
    if (reachedIndex < 0) return "waiting";
    if (index < reachedIndex) return "done";
    if (index === reachedIndex) return stopped ? "failed" : "current";
    return "waiting";
  }

  return (
    <div>
      <ol className="grid gap-1.5" aria-label="Analysis progress">
        {STAGES.map((stage, index) => {
          const state = stateOf(index);
          return (
            <li key={stage} data-state={state} className="flex items-center gap-2.5 text-sm">
              <span
                aria-hidden="true"
                className={`inline-block h-2.5 w-2.5 rounded-full ${
                  state === "done"
                    ? "bg-heat-0"
                    : state === "current"
                      ? "animate-pulse bg-accent"
                      : state === "failed"
                        ? "bg-heat-3"
                        : "border border-line"
                }`}
              />
              <span className={state === "waiting" ? "text-ink-2" : "text-ink"}>{LABELS[stage]}</span>
              <span className="sr-only">{state}</span>
            </li>
          );
        })}
      </ol>
      {current === "cancelled" && <p className="mt-2 text-sm text-ink-2">This analysis was cancelled.</p>}
      {current === "failed" && message && (
        <p role="alert" className="mt-3 rounded-md border border-heat-3 bg-heat-3-soft px-3 py-2 text-sm">
          {message}
        </p>
      )}
    </div>
  );
}
