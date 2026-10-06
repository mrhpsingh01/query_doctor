"use client";

import { useMemo, useRef, useState } from "react";
import { CandidatesList } from "@/components/candidates/CandidatesList";
import { FindingsList } from "@/components/findings/FindingsList";
import { NodeDetailPanel } from "@/components/plan-viewer/NodeDetailPanel";
import { PlanGraph } from "@/components/plan-viewer/PlanGraph";
import { formatMs } from "@/lib/format";
import { slowestNodeId, timeShare } from "@/lib/planGraph";
import type { Analysis, PlanNode } from "@/lib/types";

function findNode(node: PlanNode, id: string): PlanNode | null {
  if (node.node_id === id) return node;
  for (const child of node.children) {
    const hit = findNode(child, id);
    if (hit) return hit;
  }
  return null;
}

const describeNode = (n: PlanNode) => (n.relation_name ? `${n.node_type} on ${n.relation_name}` : n.node_type);

export function AnalysisResult({ analysis }: { analysis: Analysis }) {
  const { plan, findings } = analysis;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const graphRef = useRef<HTMLDivElement>(null);

  const selected = useMemo(() => (plan && selectedId ? findNode(plan.root, selectedId) : null), [plan, selectedId]);
  const slowest = useMemo(() => {
    if (!plan) return null;
    const id = slowestNodeId(plan);
    const node = id ? findNode(plan.root, id) : null;
    return node ? { node, share: timeShare(node, plan) } : null;
  }, [plan]);

  function select(id: string | null) {
    setSelectedId(id);
  }

  if (!plan) {
    return <p className="text-sm text-ink-2">There is no execution plan to show for this analysis.</p>;
  }

  return (
    <div className="grid gap-8">
      <section aria-label="Summary" className="grid gap-2">
        <dl className="flex flex-wrap gap-x-8 gap-y-1 text-sm">
          <div>
            <dt className="text-ink-2">Planning time</dt>
            <dd className="font-mono">{formatMs(plan.planning_time_ms)}</dd>
          </div>
          {plan.executed && (
            <div>
              <dt className="text-ink-2">Execution time</dt>
              <dd className="font-mono">{formatMs(plan.execution_time_ms)}</dd>
            </div>
          )}
        </dl>
        {plan.executed && slowest?.share != null && slowest.share > 0 ? (
          <p className="text-sm">
            About {Math.round(slowest.share * 100)}% of the time is spent in{" "}
            <button type="button" onClick={() => select(slowest.node.node_id)} className="font-semibold text-accent underline">
              {describeNode(slowest.node)}
            </button>
            .
          </p>
        ) : (
          !plan.executed && (
            <p className="text-sm text-ink-2">
              These are the planner&apos;s estimates only, so there are no real timings. Run the analysis with “Execute
              with analysis” to measure them.
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
          <PlanGraph plan={plan} findings={findings} selectedId={selectedId} onSelect={select} />
          {selected && <NodeDetailPanel node={selected} findings={findings} onClose={() => select(null)} />}
        </div>
      </section>

      <section aria-labelledby="findings-h" className="grid gap-3">
        <h2 id="findings-h" className="text-lg font-semibold">
          What we found
        </h2>
        <FindingsList
          findings={findings}
          onSelectNode={(id) => {
            select(id);
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
        <CandidatesList candidates={analysis.candidates} />
      </section>
    </div>
  );
}
