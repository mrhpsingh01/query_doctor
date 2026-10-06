import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FingerprintTable } from "@/components/workload/FingerprintTable";
import type { FingerprintHistory } from "@/lib/types";

const row = (over: Partial<FingerprintHistory> = {}): FingerprintHistory => ({
  fingerprint: "8ca0e74b3bc8a91d",
  normalized_sql: "SELECT id FROM orders WHERE customer_id = ?",
  first_seen: "2026-01-01T10:00:00Z",
  last_seen: "2026-01-04T10:00:00Z",
  analysis_count: 4,
  executed_count: 3,
  mean_execution_ms: 173,
  p95_execution_ms: 290,
  plan_change_count: 1,
  best_validation: "benchmark_validated",
  ...over,
});

describe("FingerprintTable", () => {
  it("summarises each query shape's history with its measured latency and evidence level", () => {
    render(<FingerprintTable fingerprints={[row()]} />);
    const r = screen.getAllByRole("row")[1];
    expect(r).toHaveTextContent("4");
    expect(r).toHaveTextContent("173 ms");
    expect(r).toHaveTextContent("290 ms");
    expect(r).toHaveTextContent("Benchmark-validated");
    expect(screen.getByRole("columnheader", { name: /measured p95/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /select id from orders/i })).toHaveAttribute("href", "/workload/8ca0e74b3bc8a91d");
  });

  it("shows plan changes only when there were some, and n/a for queries that were never executed", () => {
    render(<FingerprintTable fingerprints={[row({ plan_change_count: 0, mean_execution_ms: null, p95_execution_ms: null, best_validation: null })]} />);
    const r = screen.getAllByRole("row")[1];
    expect(r).toHaveTextContent("n/a");
    expect(r).not.toHaveTextContent(/validated|suggested/i);
    expect(r).toHaveTextContent("no plan changes");
  });

  it("renders only an empty message when there is no history", () => {
    render(<FingerprintTable fingerprints={[]} />);
    expect(screen.getByText(/no analyses yet/i)).toBeInTheDocument();
  });
});
