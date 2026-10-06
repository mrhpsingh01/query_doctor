import type { Finding, PlanNode, Severity } from "./types";

/** Heat ramp for plan operators: green (fine) -> yellow (possible issue) -> orange (important) -> red (bottleneck). */
export type Heat = "green" | "yellow" | "orange" | "red";

const HEAT_FOR: Record<Severity, Heat> = { info: "yellow", warning: "orange", critical: "red" };
const RANK: Record<Heat, number> = { green: 0, yellow: 1, orange: 2, red: 3 };

export function severityFor(node: PlanNode, findings: Finding[]): Heat {
  let heat: Heat = "green";
  for (const finding of findings) {
    if (!finding.node_ids.includes(node.node_id)) continue;
    const candidate = HEAT_FOR[finding.severity];
    if (RANK[candidate] > RANK[heat]) heat = candidate;
  }
  return heat;
}

// Full class names so Tailwind can see them.
export const HEAT_STYLE: Record<Heat, { bar: string; edge: string; soft: string; text: string; label: string }> = {
  green: { bar: "bg-heat-0", edge: "border-l-heat-0", soft: "bg-heat-0-soft", text: "text-heat-0", label: "fine" },
  yellow: { bar: "bg-heat-1", edge: "border-l-heat-1", soft: "bg-heat-1-soft", text: "text-heat-1", label: "possible issue" },
  orange: { bar: "bg-heat-2", edge: "border-l-heat-2", soft: "bg-heat-2-soft", text: "text-heat-2", label: "important" },
  red: { bar: "bg-heat-3", edge: "border-l-heat-3", soft: "bg-heat-3-soft", text: "text-heat-3", label: "bottleneck" },
};

export const SEVERITY_RANK: Record<Severity, number> = { critical: 0, warning: 1, info: 2 };
