"use client";

import "@xyflow/react/dist/style.css";
import { Background, Controls, MarkerType, ReactFlow } from "@xyflow/react";
import { useMemo } from "react";
import { layoutPlan } from "@/lib/planGraph";
import type { Finding, ParsedPlan } from "@/lib/types";
import { PlanNodeCard } from "./PlanNodeCard";

const nodeTypes = { plan: PlanNodeCard };

interface Props {
  plan: ParsedPlan;
  findings: Finding[];
  selectedId: string | null;
  onSelect: (nodeId: string | null) => void;
}

export function PlanGraph({ plan, findings, selectedId, onSelect }: Props) {
  const { nodes, edges } = useMemo(() => {
    const laid = layoutPlan(plan.root, findings, plan);
    return {
      nodes: laid.nodes.map((n) => ({ ...n, selected: n.id === selectedId })),
      edges: laid.edges.map((e) => ({ ...e, markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 } })),
    };
  }, [plan, findings, selectedId]);

  return (
    <div className="h-[520px] rounded-md border border-line bg-surface-2/40" role="group" aria-label="Execution plan">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, node) => onSelect(node.id)}
        onPaneClick={() => onSelect(null)}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        fitView
        fitViewOptions={{ padding: 0.15 }}
        minZoom={0.15}
        maxZoom={1.5}
      >
        <Background gap={20} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
