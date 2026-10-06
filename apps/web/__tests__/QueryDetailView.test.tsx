import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QueryDetailView } from "@/components/workload/QueryDetailView";
import type { WorkloadQueryDetail } from "@/lib/types";

// The chart needs a real layout engine; the detail view's own content is what is under test.
vi.mock("@/components/workload/TrendChart", () => ({ TrendChart: ({ trend }: { trend: unknown[] }) => <div data-testid="trend">{trend.length} points</div> }));

const detail = (over: Partial<WorkloadQueryDetail> = {}): WorkloadQueryDetail => ({
  fingerprint: "8ca0e74b3bc8a91d",
  normalized_sql: "SELECT id FROM orders WHERE customer_id = ?",
  sample_sql: "SELECT id FROM orders WHERE customer_id = $1",
  trend: [
    { snapshot_id: 1, taken_at: "2026-01-01T10:00:00Z", calls: 100, total_ms: 500, mean_ms: 5, p95_est_ms: 8 },
    { snapshot_id: 2, taken_at: "2026-01-02T10:00:00Z", calls: 200, total_ms: 1500, mean_ms: 7.5, p95_est_ms: 12 },
  ],
  analyses: [
    { id: 4, created_at: "2026-01-01T11:00:00Z", plan_fingerprint: "a", execution_time_ms: 10, validation: null },
    { id: 9, created_at: "2026-01-02T11:00:00Z", plan_fingerprint: "b", execution_time_ms: 4, validation: "benchmark_validated" },
  ],
  plan_changes: [
    { before_analysis_id: 4, after_analysis_id: 9, before_fingerprint: "a", after_fingerprint: "b", before_operators: ["Seq Scan"], after_operators: ["Index Scan"] },
  ],
  sample: null,
  ...over,
});

describe("QueryDetailView", () => {
  it("shows the query, the trend across snapshots and links to each analysis", () => {
    render(<QueryDetailView detail={detail()} />);
    expect(screen.getByText("SELECT id FROM orders WHERE customer_id = ?")).toBeInTheDocument();
    expect(screen.getByTestId("trend")).toHaveTextContent("2 points");
    const analyses = screen.getByRole("heading", { name: /analyses of this query/i }).closest("section")!;
    expect(within(analyses).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/analyses/4", "/analyses/9"]);
    expect(analyses).toHaveTextContent("Benchmark-validated");
  });

  it("describes a plan change with the operators before and after", () => {
    render(<QueryDetailView detail={detail()} />);
    const section = screen.getByRole("heading", { name: /plan changes/i }).closest("section")!;
    expect(section).toHaveTextContent("Seq Scan");
    expect(section).toHaveTextContent("Index Scan");
  });

  it("says so when the plan never changed and when there is no history", () => {
    render(<QueryDetailView detail={detail({ plan_changes: [], analyses: [], trend: [] })} />);
    expect(screen.getByText(/the plan has not changed/i)).toBeInTheDocument();
    expect(screen.getByText(/not analysed yet/i)).toBeInTheDocument();
    expect(screen.getByText(/not in any imported snapshot/i)).toBeInTheDocument();
  });

  it("links to a pre-filled analysis, with the catalog's sample parameters when it has them", () => {
    const sample = { query_id: "q", title: "Recent orders", sql: "SELECT id FROM orders WHERE customer_id = $1", params: [4242] };
    const { rerender } = render(<QueryDetailView detail={detail()} />);
    expect(screen.getByRole("link", { name: /analyze this query/i })).toHaveAttribute(
      "href",
      `/analyses/new?sql=${encodeURIComponent("SELECT id FROM orders WHERE customer_id = $1")}`,
    );
    rerender(<QueryDetailView detail={detail({ sample })} />);
    expect(screen.getByRole("link", { name: /analyze this query/i })).toHaveAttribute(
      "href",
      `/analyses/new?sql=${encodeURIComponent(sample.sql)}&params=${encodeURIComponent("[4242]")}`,
    );
    expect(screen.getByText(/sample parameters/i)).toHaveTextContent("[4242]");
  });

  it("opens a MongoDB query in MongoDB mode, from its sample or from the shape of its text", () => {
    const spec = '{"find": "orders", "filter": {"customer_id": {"$param": 1}}}';
    const sample = { query_id: "mongo_collscan_by_customer", title: "Orders of one customer", sql: spec, params: [77], engine: "mongodb" as const };
    const { rerender } = render(<QueryDetailView detail={detail({ sample })} />);
    expect(screen.getByRole("link", { name: /analyze this query/i })).toHaveAttribute(
      "href",
      `/analyses/new?sql=${encodeURIComponent(spec)}&params=${encodeURIComponent("[77]")}&engine=mongodb`,
    );
    rerender(<QueryDetailView detail={detail({ sample: null, sample_sql: '{"find": "orders", "filter": {"customer_id": 77}}' })} />);
    expect(screen.getByRole("link", { name: /analyze this query/i }).getAttribute("href")).toMatch(/&engine=mongodb$/);
  });
});
