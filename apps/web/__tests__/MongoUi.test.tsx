import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AnalysisList } from "@/components/AnalysisList";
import { CandidateCard } from "@/components/candidates/CandidateCard";
import { NodeDetailPanel } from "@/components/plan-viewer/NodeDetailPanel";
import { PlanNodeBody } from "@/components/plan-viewer/PlanNodeBody";
import { operatorHelp } from "@/lib/operators";
import { layoutPlan } from "@/lib/planGraph";
import type { AnalysisSummary, ParsedPlan } from "@/lib/types";
import { candidate, finding, node, slowPlan } from "./fixtures";

const collscan = () =>
  node({
    node_id: "0",
    node_type: "COLLSCAN",
    relation_name: "orders",
    estimated_rows: 0,
    estimated_cost: 0,
    actual_rows: 20,
    actual_time_ms: null,
    loops: null,
    exclusive_time_ms: 420,
    total_time_ms: 420,
    plan_width: null,
    docs_examined: 500_000,
    keys_examined: 0,
    raw: { stage: "COLLSCAN" },
  });
const ixscan = () => node({ ...collscan(), node_type: "IXSCAN", index_name: "status_1", docs_examined: null, keys_examined: 12_000, relation_name: null });
const visual = (engine: string) => ({ severity: "red" as const, share: 0.9, isSlowest: true, findingCount: 1, engine });

describe("PlanNodeBody for MongoDB", () => {
  it("shows documents examined, returned and the amplification, and no planner estimate", () => {
    render(<PlanNodeBody node={collscan()} data={visual("mongodb")} />);
    expect(screen.getByText("COLLSCAN")).toBeInTheDocument();
    expect(screen.getByText("orders")).toBeInTheDocument();
    expect(screen.getByText(/examined 500k docs/)).toBeInTheDocument();
    expect(screen.getByText(/returned 20/)).toBeInTheDocument();
    expect(screen.getByText(/25k× per result/)).toBeInTheDocument();
    expect(screen.queryByText(/\best\b/)).not.toBeInTheDocument();
    expect(screen.queryByText(/actual/)).not.toBeInTheDocument();
    expect(screen.getByText("420 ms")).toBeInTheDocument();
  });

  it("counts index keys for an index scan", () => {
    render(<PlanNodeBody node={ixscan()} data={visual("mongodb")} />);
    expect(screen.getByText("status_1")).toBeInTheDocument();
    expect(screen.getByText(/examined 12k keys/)).toBeInTheDocument();
  });

  it("shows no amplification when nothing was thrown away, and nothing for an explain-only node", () => {
    const exact = { ...collscan(), docs_examined: 20 };
    const { rerender } = render(<PlanNodeBody node={exact} data={visual("mongodb")} />);
    expect(screen.getByText(/examined 20 docs/)).toBeInTheDocument();
    expect(screen.queryByText(/per result/)).not.toBeInTheDocument();
    rerender(<PlanNodeBody node={{ ...collscan(), actual_rows: null, docs_examined: null, keys_examined: null }} data={visual("mongodb")} />);
    expect(screen.queryByText(/examined/)).not.toBeInTheDocument();
    expect(screen.queryByText(/returned/)).not.toBeInTheDocument();
  });

  it("keeps the PostgreSQL face unchanged, estimate included", () => {
    render(<PlanNodeBody node={slowPlan().root.children[0]} data={{ severity: "red", share: 0.9, isSlowest: true, findingCount: 0 }} />);
    expect(screen.getByText(/est 10\.9k/)).toBeInTheDocument();
    expect(screen.queryByText(/examined/)).not.toBeInTheDocument();
  });
});

describe("layoutPlan", () => {
  it("tags every node with the plan's engine", () => {
    const plan = { ...slowPlan(), engine: "mongodb" } as ParsedPlan;
    expect(layoutPlan(plan.root, [], plan).nodes.every((n) => n.data.engine === "mongodb")).toBe(true);
    expect(layoutPlan(slowPlan().root, [], slowPlan()).nodes.every((n) => n.data.engine === "postgres")).toBe(true);
  });
});

describe("NodeDetailPanel for MongoDB", () => {
  it("lists examined documents and keys and returned documents instead of estimates", () => {
    render(<NodeDetailPanel node={collscan()} findings={[finding({ node_ids: ["0"] })]} onClose={() => {}} engine="mongodb" />);
    const facts = screen.getByRole("region", { name: /in this query/i });
    expect(within(facts).getByText("Documents examined")).toBeInTheDocument();
    expect(within(facts).getByText("500k")).toBeInTheDocument();
    expect(within(facts).getByText("Documents returned")).toBeInTheDocument();
    expect(within(facts).queryByText("Estimated rows")).not.toBeInTheDocument();
    expect(within(facts).queryByText("Loops")).not.toBeInTheDocument();
    expect(screen.getByText(/reads every document in the collection/i)).toBeInTheDocument();
  });

  it("shows index keys examined for an index scan", () => {
    render(<NodeDetailPanel node={ixscan()} findings={[]} onClose={() => {}} engine="mongodb" />);
    expect(screen.getByText("Index keys examined")).toBeInTheDocument();
    expect(screen.getByText("12k")).toBeInTheDocument();
  });

  it("keeps the PostgreSQL facts for PostgreSQL", () => {
    render(<NodeDetailPanel node={slowPlan().root.children[0]} findings={[]} onClose={() => {}} />);
    expect(screen.getByText("Estimated rows")).toBeInTheDocument();
    expect(screen.queryByText("Documents examined")).not.toBeInTheDocument();
  });
});

describe("operatorHelp", () => {
  it.each(["COLLSCAN", "IXSCAN", "FETCH", "SORT", "$group", "$lookup", "$match"])("describes the MongoDB stage %s", (stage) => {
    expect(operatorHelp(stage)).not.toMatch(/no built-in description/i);
  });
});

describe("CandidateCard for MongoDB", () => {
  const mongoIdea = () =>
    candidate({
      ddl: 'db.orders.createIndex({"customer_id": 1})',
      columns: [{ name: "customer_id", desc: false }],
      include: [],
      validation: "suggested",
      planner_score: null,
      comparison: null,
      rationale: ["MongoDB has no hypothetical indexes, so this stays a suggestion until a benchmark measures it."],
    });

  it("shows the createIndex command verbatim with the suggested evidence level and no planner score", () => {
    render(<CandidateCard candidate={mongoIdea()} />);
    expect(screen.getByText('db.orders.createIndex({"customer_id": 1})')).toBeInTheDocument();
    expect(screen.getByText("Suggested")).toBeInTheDocument();
    expect(screen.queryByRole("meter", { name: /planner score/i })).not.toBeInTheDocument();
    expect(screen.getByText(/no hypothetical indexes/i)).toBeInTheDocument();
  });

  it("describes the benchmark copy in MongoDB terms before it can be run", async () => {
    const user = userEvent.setup();
    const approve = vi.fn();
    render(<CandidateCard candidate={mongoIdea()} onApprove={approve} engine="mongodb" />);
    expect(screen.getByText(/temporary copy of the collections/i)).toBeInTheDocument();
    const run = screen.getByRole("button", { name: /run benchmark/i });
    expect(run).toBeDisabled();
    await user.click(screen.getByRole("checkbox"));
    await user.click(run);
    expect(approve).toHaveBeenCalledTimes(1);
  });
});

describe("AnalysisList engine badge", () => {
  const row = (over: Partial<AnalysisSummary>): AnalysisSummary => ({
    id: 3, status: "completed", execute: false, sql: '{"find": "orders"}', fingerprint: "a1b2c3d4e5f60718", execution_time_ms: null,
    finding_count: 1, critical_count: 0, best_validation: null, created_at: "2026-01-01T10:00:00Z", ...over,
  });

  it("labels each analysis with its database", () => {
    render(<AnalysisList analyses={[row({ engine: "mongodb" }), row({ id: 4, engine: "postgres" }), row({ id: 5 })]} empty="none" />);
    const links = screen.getAllByRole("link");
    expect(links[0]).toHaveTextContent("MongoDB");
    expect(links[1]).toHaveTextContent("PostgreSQL");
    expect(links[2]).toHaveTextContent("PostgreSQL"); // older rows without an engine are PostgreSQL
  });
});
