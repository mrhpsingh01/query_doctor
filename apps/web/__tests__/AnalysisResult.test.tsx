import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AnalysisResult } from "@/components/AnalysisResult";
import type { Analysis } from "@/lib/types";
import { candidate, finding, slowPlan } from "./fixtures";

// React Flow needs real layout; the graph is replaced by one button per operator.
vi.mock("@/components/plan-viewer/PlanGraph", () => ({
  PlanGraph: ({ plan, onSelect }: { plan: { root: { node_id: string; node_type: string; children: { node_id: string; node_type: string }[] } }; onSelect: (id: string) => void }) => (
    <div>
      {[plan.root, ...plan.root.children].map((n) => (
        <button key={n.node_id} onClick={() => onSelect(n.node_id)}>
          graph:{n.node_type}
        </button>
      ))}
    </div>
  ),
}));

function analysis(overrides: Partial<Analysis> = {}): Analysis {
  return {
    id: 1, project_id: 1, status: "completed", stage: "testing_candidates", execute: true, error: null,
    sql: "SELECT 1", normalized_sql: "SELECT ?", fingerprint: "f", params: [], plan: slowPlan(),
    planning_time_ms: 0.21, execution_time_ms: 742.1, findings: [finding()], candidates: [],
    created_at: "2026-01-01T00:00:00Z", started_at: null, finished_at: null, ...overrides,
  };
}

describe("AnalysisResult", () => {
  it("calls out the slowest operator and the timings for an executed plan", () => {
    render(<AnalysisResult analysis={analysis()} />);
    const callout = screen.getByText(
      (_, el) => el?.tagName === "P" && /99% of the time is spent in Seq Scan on orders/.test(el.textContent ?? ""),
    );
    expect(callout).toBeInTheDocument();
    expect(screen.getByText("742 ms")).toBeInTheDocument();
  });

  it("opens the operator details when a graph node is clicked, and closes them again", async () => {
    const user = userEvent.setup();
    render(<AnalysisResult analysis={analysis()} />);
    expect(screen.queryByRole("complementary", { name: /operator details/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "graph:Seq Scan" }));
    expect(screen.getByRole("complementary", { name: /operator details/i })).toHaveTextContent("Seq Scan on orders");
    await user.click(screen.getByRole("button", { name: /close details/i }));
    expect(screen.queryByRole("complementary", { name: /operator details/i })).not.toBeInTheDocument();
  });

  it("opens the details for the related node from a finding's Show in plan button", async () => {
    const user = userEvent.setup();
    render(<AnalysisResult analysis={analysis()} />);
    await user.click(screen.getByRole("button", { name: /show in plan/i }));
    expect(screen.getByRole("complementary", { name: /operator details/i })).toHaveTextContent("Seq Scan on orders");
  });

  it("explains that planner-only plans carry estimates, not timings", () => {
    const planned = slowPlan();
    planned.executed = false;
    planned.execution_time_ms = null;
    render(<AnalysisResult analysis={analysis({ execute: false, plan: planned, execution_time_ms: null })} />);
    expect(screen.getByText(/estimates only/i)).toBeInTheDocument();
    expect(screen.queryByText(/of the time is spent/i)).not.toBeInTheDocument();
  });

  it("lists index ideas with their evidence level under the findings", () => {
    render(<AnalysisResult analysis={analysis({ candidates: [candidate()] })} />);
    expect(screen.getByRole("heading", { name: /index ideas/i })).toBeInTheDocument();
    expect(screen.getByText("Planner-validated")).toBeInTheDocument();
  });

  it("shows a message instead of a graph when the plan is missing", () => {
    render(<AnalysisResult analysis={analysis({ plan: null, findings: [] })} />);
    expect(screen.getByText(/no execution plan/i)).toBeInTheDocument();
  });
});
