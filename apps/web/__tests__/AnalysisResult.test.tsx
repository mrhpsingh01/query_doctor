import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AnalysisResult } from "@/components/AnalysisResult";
import type { Analysis } from "@/lib/types";
import { api } from "@/lib/api";
import { benchmarkRun, candidate, finding, slowPlan } from "./fixtures";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      approveBenchmark: vi.fn().mockResolvedValue({ id: 9, status: "queued" }),
      cancelBenchmark: vi.fn().mockResolvedValue({ id: 9, status: "cancelled" }),
    },
  };
});

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
    benchmarks: [],
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

  it("links to Markdown and HTML report downloads", () => {
    render(<AnalysisResult analysis={analysis()} />);
    expect(screen.getByRole("link", { name: /markdown/i })).toHaveAttribute("href", "/api/analyses/1/report?format=md");
    expect(screen.getByRole("link", { name: /html/i })).toHaveAttribute("href", "/api/analyses/1/report?format=html");
  });

  describe("benchmarks", () => {
    it("approves a benchmark only after confirmation, then shows it waiting and refreshes later", async () => {
      const user = userEvent.setup();
      vi.mocked(api.approveBenchmark).mockClear();
      render(<AnalysisResult analysis={analysis({ candidates: [candidate({ id: 5 })] })} />);
      expect(api.approveBenchmark).not.toHaveBeenCalled();
      await user.click(screen.getByRole("checkbox", { name: /real index in an isolated copy/i }));
      await user.click(screen.getByRole("button", { name: /run benchmark/i }));
      expect(api.approveBenchmark).toHaveBeenCalledWith(1, 5);
      expect(await screen.findByText(/waiting for a free benchmark slot/i)).toBeInTheDocument();
    });

    it("prefers the server's newer state over the run it was tracking locally under the same id", async () => {
      const user = userEvent.setup();
      const base = analysis({ candidates: [candidate({ id: 5 })] });
      const { rerender } = render(<AnalysisResult analysis={base} />);
      await user.click(screen.getByRole("checkbox", { name: /real index in an isolated copy/i }));
      await user.click(screen.getByRole("button", { name: /run benchmark/i }));
      expect(await screen.findByText(/waiting for a free benchmark slot/i)).toBeInTheDocument();

      // the page reloads the analysis once the benchmark finishes; the same run id now arrives as completed
      rerender(<AnalysisResult analysis={{ ...base, benchmarks: [benchmarkRun({ id: 9, candidate_id: 5 })] }} />);
      expect(screen.getByText("94.5% faster")).toBeInTheDocument();
      expect(screen.queryByText(/waiting for a free benchmark slot/i)).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: /run benchmark/i })).toBeInTheDocument(); // no longer "running"
    });

    it("shows an existing benchmark's result under its candidate", () => {
      render(<AnalysisResult analysis={analysis({ candidates: [candidate({ id: 1 })], benchmarks: [benchmarkRun()] })} />);
      expect(screen.getByText("94.5% faster")).toBeInTheDocument();
    });

    it("shows the latest benchmark when a candidate has been benchmarked more than once", () => {
      const older = benchmarkRun({ id: 2, status: "failed", result: null, error: "older failure" });
      render(<AnalysisResult analysis={analysis({ candidates: [candidate({ id: 1 })], benchmarks: [older, benchmarkRun({ id: 3 })] })} />);
      expect(screen.queryByText("older failure")).not.toBeInTheDocument();
      expect(screen.getByText("94.5% faster")).toBeInTheDocument();
    });

    it("explains why an approval was refused", async () => {
      const { ApiError } = await import("@/lib/api");
      vi.mocked(api.approveBenchmark).mockRejectedValueOnce(new ApiError("the analysis has not finished yet", 409));
      const user = userEvent.setup();
      render(<AnalysisResult analysis={analysis({ candidates: [candidate({ id: 5 })] })} />);
      await user.click(screen.getByRole("checkbox", { name: /real index in an isolated copy/i }));
      await user.click(screen.getByRole("button", { name: /run benchmark/i }));
      expect(await screen.findByRole("alert")).toHaveTextContent("the analysis has not finished yet");
    });

    it("does not offer a benchmark for a ruled-out idea or while one is running", () => {
      render(
        <AnalysisResult
          analysis={analysis({
            candidates: [candidate({ id: 1 }), candidate({ id: 2, dropped: true, drop_reason: "dup", rank: 0 })],
            benchmarks: [benchmarkRun({ status: "running", result: null })],
          })}
        />,
      );
      expect(screen.getByRole("button", { name: /benchmark running/i })).toBeDisabled();
      expect(screen.getAllByRole("button", { name: /run benchmark|benchmark running/i })).toHaveLength(1);
    });
  });
});
