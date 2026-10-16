import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QueryTable } from "@/components/workload/QueryTable";
import { workloadQuery } from "./fixtures";

describe("QueryTable", () => {
  it("shows rank, calls, mean, total and a share bar for each query", () => {
    render(<QueryTable queries={[workloadQuery()]} />);
    const row = screen.getAllByRole("row")[1];
    expect(within(row).getByText("1")).toBeInTheDocument();
    expect(row).toHaveTextContent("1,500");
    expect(row).toHaveTextContent("6 ms");
    expect(row).toHaveTextContent("9 s");
    const meter = within(row).getByRole("meter", { name: /share of total database time/i });
    expect(meter).toHaveAttribute("aria-valuenow", "72");
    expect(row).toHaveTextContent("72%");
  });

  it("labels the p95 as an estimate", () => {
    render(<QueryTable queries={[workloadQuery()]} />);
    const header = screen.getByRole("columnheader", { name: /estimated p95/i });
    expect(screen.queryByRole("columnheader", { name: /^p95$/i })).not.toBeInTheDocument();
    expect(header).toHaveAttribute("title", expect.stringMatching(/no percentiles/i));
  });

  it("labels MongoDB profiler p95 as an observed sample", () => {
    render(<QueryTable queries={[workloadQuery({ p95_observed_ms: 31 })]} engine="mongodb" />);
    expect(screen.getByRole("columnheader", { name: /sample p95/i })).toHaveAttribute("title", expect.stringMatching(/bounded profiler sample/i));
    expect(screen.getAllByRole("row")[1]).toHaveTextContent("31 ms");
  });

  it("links each query to its detail page and to a pre-filled analysis", () => {
    const q = workloadQuery();
    render(<QueryTable queries={[q]} />);
    expect(screen.getByRole("link", { name: /select id, total_amount from orders/i })).toHaveAttribute("href", `/workload/${q.fingerprint}`);
    expect(screen.getByRole("link", { name: /analyze/i })).toHaveAttribute("href", `/analyses/new?sql=${encodeURIComponent(q.sample_sql)}`);
  });

  it("keeps the order it is given and shows an empty message for no queries", () => {
    const { rerender } = render(<QueryTable queries={[workloadQuery({ rank: 1 }), workloadQuery({ fingerprint: "b", rank: 2, share: 0.1 })]} />);
    expect(screen.getAllByRole("row")).toHaveLength(3);
    rerender(<QueryTable queries={[]} />);
    expect(screen.getByText(/no queries/i)).toBeInTheDocument();
  });
});
