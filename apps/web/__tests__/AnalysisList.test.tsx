import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AnalysisList } from "@/components/AnalysisList";
import type { AnalysisSummary } from "@/lib/types";

const row = (over: Partial<AnalysisSummary> = {}): AnalysisSummary => ({
  id: 7, status: "completed", execute: true, sql: "SELECT id FROM orders WHERE customer_id = $1",
  fingerprint: "a1b2c3d4e5f60718", execution_time_ms: 742, finding_count: 2, critical_count: 1,
  best_validation: "benchmark_validated", created_at: "2026-01-01T10:00:00Z", ...over,
});

describe("AnalysisList", () => {
  it("shows the query, status, fingerprint and the best evidence level reached", () => {
    render(<AnalysisList analyses={[row()]} empty="nothing" />);
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "/analyses/7");
    expect(link).toHaveTextContent("SELECT id FROM orders");
    expect(link).toHaveTextContent("completed");
    expect(link).toHaveTextContent("a1b2c3d4");
    expect(link).toHaveTextContent("Benchmark-validated");
    expect(link).toHaveTextContent("1 critical");
    expect(link).toHaveTextContent("742 ms");
  });

  it("omits the evidence level when there are no index ideas and shows the empty message", () => {
    const { rerender } = render(<AnalysisList analyses={[row({ best_validation: null })]} empty="nothing" />);
    expect(screen.getByRole("link")).not.toHaveTextContent(/validated|suggested/i);
    rerender(<AnalysisList analyses={[]} empty="No analyses yet." />);
    expect(screen.getByText("No analyses yet.")).toBeInTheDocument();
  });
});
