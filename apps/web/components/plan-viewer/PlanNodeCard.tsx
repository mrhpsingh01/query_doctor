"use client";

import { Handle, type NodeProps, Position } from "@xyflow/react";
import type { PlanFlowNode } from "@/lib/planGraph";
import { PlanNodeBody } from "./PlanNodeBody";

/** React Flow wrapper: arrows point up, from each operator to the one that consumes its rows. */
export function PlanNodeCard({ data, selected }: NodeProps<PlanFlowNode>) {
  return (
    <div className={selected ? "rounded outline outline-2 outline-offset-2 outline-accent" : ""} style={{ width: 248 }}>
      <Handle type="target" position={Position.Bottom} isConnectable={false} className="!opacity-0" />
      <PlanNodeBody node={data.node} data={data} />
      <Handle type="source" position={Position.Top} isConnectable={false} className="!opacity-0" />
    </div>
  );
}
