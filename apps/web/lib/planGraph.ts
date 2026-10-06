import type { Edge, Node } from "@xyflow/react";
import { type Heat, severityFor } from "./severity";
import type { Finding, ParsedPlan, PlanNode } from "./types";

export const NODE_WIDTH = 248;
export const NODE_HEIGHT = 124;

export interface NodeVisual {
  severity: Heat;
  /** Share of the whole query's time spent in this operator itself; null for planner-only plans. */
  share: number | null;
  isSlowest: boolean;
  findingCount: number;
  /** "mongodb" shows documents examined instead of planner estimates. Absent means PostgreSQL. */
  engine?: string;
}
export type PlanNodeData = NodeVisual & { node: PlanNode } & Record<string, unknown>;
export type PlanFlowNode = Node<PlanNodeData, "plan">;

function* walk(node: PlanNode): Generator<PlanNode> {
  yield node;
  for (const child of node.children) yield* walk(child);
}

function planTotalMs(plan: ParsedPlan): number | null {
  // MongoDB reports per-stage times as estimates that do not add up to the server's own total, so a stage's share is
  // taken among the stages (the root's cumulative time), which keeps the shares comparable and at most 100%.
  if (plan.engine === "mongodb" && plan.root.total_time_ms != null) return plan.root.total_time_ms;
  if (plan.execution_time_ms !== null) return plan.execution_time_ms;
  const { actual_time_ms, loops } = plan.root;
  return actual_time_ms === null || loops === null ? null : actual_time_ms * loops;
}

export function timeShare(node: PlanNode, plan: ParsedPlan): number | null {
  const total = planTotalMs(plan);
  if (node.exclusive_time_ms === null || total === null) return null;
  return total === 0 ? 0 : Math.min(node.exclusive_time_ms / total, 1);
}

/** The operator that spent the most time itself (children excluded); null when the plan was not executed. */
export function slowestNodeId(plan: ParsedPlan): string | null {
  let best: PlanNode | null = null;
  for (const node of walk(plan.root)) {
    if (node.exclusive_time_ms === null) continue;
    if (best === null || node.exclusive_time_ms > (best.exclusive_time_ms ?? 0)) best = node;
  }
  return best?.node_id ?? null;
}

const GAP_X = 36;
const GAP_Y = 64;
const MARGIN = 16;

/** Width of the horizontal band a subtree needs: its leaves side by side, never narrower than one card. */
function subtreeWidth(node: PlanNode): number {
  if (node.children.length === 0) return NODE_WIDTH;
  const children = node.children.reduce((sum, child) => sum + subtreeWidth(child), 0) + GAP_X * (node.children.length - 1);
  return Math.max(NODE_WIDTH, children);
}

/** Deterministic tidy-tree layout: children left to right in plan order, each parent centred over its children. */
function place(node: PlanNode, left: number, depth: number, out: Map<string, { x: number; y: number }>): void {
  const width = subtreeWidth(node);
  const y = MARGIN + depth * (NODE_HEIGHT + GAP_Y);
  if (node.children.length === 0) {
    out.set(node.node_id, { x: left + (width - NODE_WIDTH) / 2, y });
    return;
  }
  let cursor = left;
  for (const child of node.children) {
    place(child, cursor, depth + 1, out);
    cursor += subtreeWidth(child) + GAP_X;
  }
  const first = out.get(node.children[0].node_id)!;
  const last = out.get(node.children[node.children.length - 1].node_id)!;
  out.set(node.node_id, { x: (first.x + last.x) / 2, y });
}

/**
 * Lay the plan out with the root on top and leaf scans at the bottom. Edges run from child to parent, so arrows
 * follow the data as it flows up to the result.
 */
export function layoutPlan(root: PlanNode, findings: Finding[], plan: ParsedPlan): { nodes: PlanFlowNode[]; edges: Edge[] } {
  const positions = new Map<string, { x: number; y: number }>();
  place(root, MARGIN, 0, positions);

  const all = [...walk(root)];
  const edges: Edge[] = all.flatMap((node) =>
    node.children.map((child) => ({ id: `${child.node_id}->${node.node_id}`, source: child.node_id, target: node.node_id })),
  );

  const slowest = slowestNodeId(plan);
  const nodes: PlanFlowNode[] = all.map((node) => ({
    id: node.node_id,
    type: "plan",
    position: positions.get(node.node_id)!,
    data: {
      node,
      severity: severityFor(node, findings),
      share: timeShare(node, plan),
      isSlowest: node.node_id === slowest,
      findingCount: findings.filter((f) => f.node_ids.includes(node.node_id)).length,
      engine: plan.engine ?? "postgres",
    },
  }));
  return { nodes, edges };
}
