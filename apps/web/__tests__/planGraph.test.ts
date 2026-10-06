import { describe, expect, it } from "vitest";
import { layoutPlan, slowestNodeId, timeShare } from "@/lib/planGraph";
import { severityFor } from "@/lib/severity";
import { finding, node, slowPlan } from "./fixtures";

describe("layoutPlan", () => {
  it("creates one flow node per plan node, with edges from child up to parent", () => {
    const plan = slowPlan();
    const { nodes, edges } = layoutPlan(plan.root, [], plan);
    expect(nodes.map((n) => n.id)).toEqual(["0", "0.0"]);
    expect(edges).toEqual([expect.objectContaining({ id: "0.0->0", source: "0.0", target: "0" })]);
  });

  it("puts the root above its children and gives every node a distinct position", () => {
    const wide = node({
      node_id: "0",
      children: [node({ node_id: "0.0", parent_id: "0" }), node({ node_id: "0.1", parent_id: "0" }), node({ node_id: "0.2", parent_id: "0" })],
    });
    const { nodes } = layoutPlan(wide, [], { ...slowPlan(), root: wide });
    const at = (id: string) => nodes.find((n) => n.id === id)!.position;
    expect(at("0").y).toBeLessThan(at("0.0").y);
    expect(new Set(nodes.map((n) => `${n.position.x},${n.position.y}`)).size).toBe(4);
    expect(at("0.0").x).toBeLessThan(at("0.2").x);
  });

  it("attaches severity, time share and the slowest flag to each node's data", () => {
    const plan = slowPlan();
    const { nodes } = layoutPlan(plan.root, [finding()], plan);
    const scan = nodes.find((n) => n.id === "0.0")!.data;
    const sort = nodes.find((n) => n.id === "0")!.data;
    expect(scan.severity).toBe("red");
    expect(scan.isSlowest).toBe(true);
    expect(scan.share).toBeCloseTo(735.2 / 742.1, 3);
    expect(sort.severity).toBe("green");
    expect(sort.isSlowest).toBe(false);
  });

  it("copes with a single-node plan", () => {
    const only = node();
    expect(layoutPlan(only, [], { ...slowPlan(), root: only }).nodes).toHaveLength(1);
  });
});

describe("severityFor", () => {
  const n = node({ node_id: "0.0" });
  it.each([
    [[finding({ severity: "critical", node_ids: ["0.0"] })], "red"],
    [[finding({ severity: "warning", node_ids: ["0.0"] })], "orange"],
    [[finding({ severity: "info", node_ids: ["0.0"] })], "yellow"],
    [[], "green"],
    [[finding({ severity: "critical", node_ids: ["9.9"] })], "green"],
  ])("maps %j to %s", (findings, expected) => {
    expect(severityFor(n, findings)).toBe(expected);
  });

  it("uses the most severe finding when several apply", () => {
    const findings = [finding({ severity: "info", node_ids: ["0.0"] }), finding({ severity: "critical", node_ids: ["0.0", "1"] })];
    expect(severityFor(n, findings)).toBe("red");
  });
});

describe("slowestNodeId and timeShare", () => {
  it("picks the node with the greatest exclusive time", () => {
    expect(slowestNodeId(slowPlan())).toBe("0.0");
  });

  it("is null for planner-only plans", () => {
    const only = node({ exclusive_time_ms: null, actual_time_ms: null });
    expect(slowestNodeId({ ...slowPlan(), root: only, executed: false })).toBeNull();
    expect(timeShare(only, { ...slowPlan(), root: only, executed: false })).toBeNull();
  });

  it("shares a MongoDB plan's time among its own stages, whose times are estimates of the server's slot-based execution", () => {
    const inner = node({ node_id: "0.0", parent_id: "0", node_type: "FETCH", exclusive_time_ms: 13, total_time_ms: 23 });
    const root = node({ node_id: "0", node_type: "SORT", exclusive_time_ms: 12, total_time_ms: 35, children: [inner] });
    const plan = { ...slowPlan(), root, engine: "mongodb", execution_time_ms: 458 };
    expect(timeShare(inner, plan)).toBeCloseTo(13 / 35);
    expect(timeShare(root, plan)).toBeCloseTo(12 / 35);
    // PostgreSQL keeps the whole-query time as the denominator
    expect(timeShare(inner, { ...plan, engine: "postgres" })).toBeCloseTo(13 / 458);
  });

  it("never divides by zero", () => {
    const zero = node({ exclusive_time_ms: 0 });
    expect(timeShare(zero, { ...slowPlan(), root: zero, execution_time_ms: 0 })).toBe(0);
  });
});
