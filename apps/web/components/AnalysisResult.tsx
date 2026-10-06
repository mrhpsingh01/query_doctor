"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { BenchmarkPanel } from "@/components/benchmarks/BenchmarkPanel";
import { CandidatesList } from "@/components/candidates/CandidatesList";
import { ExplainPanel } from "@/components/explain/ExplainPanel";
import { FindingsList } from "@/components/findings/FindingsList";
import { NodeDetailPanel } from "@/components/plan-viewer/NodeDetailPanel";
import { PlanGraph } from "@/components/plan-viewer/PlanGraph";
import { ApiError, api } from "@/lib/api";
import { formatMs } from "@/lib/format";
import { slowestNodeId, timeShare } from "@/lib/planGraph";
import type { Analysis, BenchmarkRun, Candidate, PlanNode } from "@/lib/types";

function findNode(node: PlanNode, id: string): PlanNode | null {
  if (node.node_id === id) return node;
  for (const child of node.children) {
    const hit = findNode(child, id);
    if (hit) return hit;
  }
  return null;
}

const describeNode = (n: PlanNode) => (n.relation_name ? `${n.node_type} on ${n.relation_name}` : n.node_type);
const isActive = (run: BenchmarkRun) => run.status === "queued" || run.status === "running";

interface Props {
  analysis: Analysis;
  /** Reload the analysis (after a benchmark finishes, its evidence level may have changed). */
  onRefresh?: () => void;
}

export function AnalysisResult({ analysis, onRefresh }: Props) {
  const { plan, findings } = analysis;
  const engine = analysis.engine ?? plan?.engine ?? "postgres";
  const mongo = engine === "mongodb";
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [approved, setApproved] = useState<Record<number, BenchmarkRun>>({});
  const [approveError, setApproveError] = useState<string | null>(null);
  const graphRef = useRef<HTMLDivElement>(null);

  const selected = useMemo(() => (plan && selectedId ? findNode(plan.root, selectedId) : null), [plan, selectedId]);
  const slowest = useMemo(() => {
    if (!plan) return null;
    const id = slowestNodeId(plan);
    const node = id ? findNode(plan.root, id) : null;
    return node ? { node, share: timeShare(node, plan) } : null;
  }, [plan]);

  // The newest benchmark per candidate: from the server, or the one just approved in this session.
  const latestRun = useMemo(() => {
    const byCandidate = new Map<number, BenchmarkRun>();
    // Server runs come last so that, for the same id, the server's state replaces what was tracked locally.
    for (const run of [...Object.values(approved), ...analysis.benchmarks]) {
      const current = byCandidate.get(run.candidate_id);
      if (!current || run.id >= current.id) byCandidate.set(run.candidate_id, run);
    }
    return byCandidate;
  }, [analysis.benchmarks, approved]);

  const approve = useCallback(
    async (candidate: Candidate) => {
      setApproveError(null);
      try {
        const { id, status } = await api.approveBenchmark(analysis.id, candidate.id);
        setApproved((prev) => ({
          ...prev,
          [candidate.id]: {
            id, analysis_id: analysis.id, candidate_id: candidate.id, status: status as BenchmarkRun["status"],
            stage: null, config: {}, result: null, error: null, created_at: new Date().toISOString(), started_at: null, finished_at: null,
          },
        }));
      } catch (e) {
        setApproveError(e instanceof ApiError ? e.message : "The benchmark could not be started. Try again.");
      }
    },
    [analysis.id],
  );

  const cancel = useCallback(async (run: BenchmarkRun) => {
    try {
      await api.cancelBenchmark(run.id);
      setApproved((prev) => ({ ...prev, [run.candidate_id]: { ...run, status: "cancelled" } }));
    } catch (e) {
      setApproveError(e instanceof ApiError ? e.message : "The benchmark could not be cancelled.");
    }
  }, []);

  if (!plan) {
    return <p className="text-sm text-ink-2">There is no execution plan to show for this analysis.</p>;
  }

  return (
    <div className="grid gap-8">
      <section aria-label="Summary" className="grid gap-2">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <dl className="flex flex-wrap gap-x-8 gap-y-1 text-sm">
            {!mongo && (
              <div>
                <dt className="text-ink-2">Planning time</dt>
                <dd className="font-mono">{formatMs(plan.planning_time_ms)}</dd>
              </div>
            )}
            {plan.executed && (
              <div>
                <dt className="text-ink-2">Execution time</dt>
                <dd className="font-mono">{formatMs(plan.execution_time_ms)}</dd>
              </div>
            )}
          </dl>
          <p className="flex items-center gap-3 text-sm">
            <span className="text-ink-2">Export report</span>
            <a href={api.reportUrl(analysis.id, "md")} download className="font-medium text-accent underline">
              Markdown
            </a>
            <a href={api.reportUrl(analysis.id, "html")} download className="font-medium text-accent underline">
              HTML
            </a>
          </p>
        </div>
        {plan.executed && slowest?.share != null && slowest.share > 0 ? (
          <p className="text-sm">
            About {Math.round(slowest.share * 100)}% of the time is spent in{" "}
            <button type="button" onClick={() => setSelectedId(slowest.node.node_id)} className="font-semibold text-accent underline">
              {describeNode(slowest.node)}
            </button>
            .
          </p>
        ) : (
          !plan.executed && (
            <p className="text-sm text-ink-2">
              {mongo
                ? "This is the plan MongoDB chose, without running the query, so there are no real timings or document counts. Run the analysis with “Execute with analysis” to measure them."
                : "These are the planner's estimates only, so there are no real timings. Run the analysis with “Execute with analysis” to measure them."}
            </p>
          )
        )}
      </section>

      <section aria-labelledby="plan-h" className="grid gap-3" ref={graphRef}>
        <h2 id="plan-h" className="text-lg font-semibold">
          Execution plan
        </h2>
        <p className="text-sm text-ink-2">
          Rows flow up from the scans at the bottom to the result at the top. The bar on each step shows how much of the
          query&apos;s time it spent itself. Select a step for details.
        </p>
        <div className={`grid gap-4 ${selected ? "lg:grid-cols-[minmax(0,1fr)_380px]" : ""}`}>
          <PlanGraph plan={plan} findings={findings} selectedId={selectedId} onSelect={setSelectedId} />
          {selected && <NodeDetailPanel node={selected} findings={findings} onClose={() => setSelectedId(null)} engine={engine} />}
        </div>
      </section>

      <section aria-labelledby="findings-h" className="grid gap-3">
        <h2 id="findings-h" className="text-lg font-semibold">
          What we found
        </h2>
        <FindingsList
          findings={findings}
          onSelectNode={(id) => {
            setSelectedId(id);
            graphRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
          }}
        />
      </section>

      <section aria-labelledby="ideas-h" className="grid gap-3">
        <h2 id="ideas-h" className="text-lg font-semibold">
          Index ideas
        </h2>
        <p className="text-sm text-ink-2">
          Each idea says how well it is backed: suggested from the query, picked by the planner in a what-if test, or
          measured faster in a benchmark. Nothing is ever created for you.
        </p>
        {approveError && (
          <p role="alert" className="rounded-md border border-heat-3 bg-heat-3-soft px-3 py-2 text-sm">
            {approveError}
          </p>
        )}
        <CandidatesList
          candidates={analysis.candidates}
          analysisId={analysis.id}
          engine={engine}
          onApprove={analysis.connection_id ? undefined : approve}
          busyCandidateId={[...latestRun.values()].find(isActive)?.candidate_id ?? null}
          renderExtra={(c) => {
            const run = latestRun.get(c.id);
            return run ? <BenchmarkPanel key={run.id} run={run} onCancel={cancel} onFinished={onRefresh} /> : null;
          }}
        />
      </section>

      <ExplainPanel analysisId={analysis.id} />
    </div>
  );
}
