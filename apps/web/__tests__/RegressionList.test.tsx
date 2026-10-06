import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RegressionList } from "@/components/workload/RegressionList";
import { dashboard, regression } from "./fixtures";

describe("RegressionList", () => {
  it("says what the query was, what it is now and how many times slower", () => {
    render(<RegressionList regressions={[regression()]} queries={dashboard().queries} comparing />);
    const item = screen.getByRole("listitem");
    expect(item).toHaveTextContent("was 5 ms, now 25 ms (5× slower)");
    expect(item).toHaveTextContent("60 calls");
    expect(screen.getByRole("link")).toHaveAttribute("href", "/workload/8ca0e74b3bc8a91d");
    expect(item).toHaveTextContent("FROM orders WHERE customer_id");
  });

  it("rounds the factor to one decimal and drops a trailing zero", () => {
    render(<RegressionList regressions={[regression({ ratio: 1.84, baseline_mean_ms: 10, recent_mean_ms: 18.4 })]} queries={[]} comparing />);
    expect(screen.getByRole("listitem")).toHaveTextContent("(1.8× slower)");
  });

  it("falls back to the fingerprint when the query text is not in the ranked list", () => {
    render(<RegressionList regressions={[regression({ fingerprint: "abcdef0123456789" })]} queries={[]} comparing />);
    expect(screen.getByRole("link")).toHaveTextContent("abcdef01");
  });

  it("tells the person what to do when there is nothing to compare, and when nothing regressed", () => {
    const { rerender } = render(<RegressionList regressions={[]} queries={[]} comparing={false} />);
    expect(screen.getByText(/import a second snapshot/i)).toBeInTheDocument();
    rerender(<RegressionList regressions={[]} queries={[]} comparing />);
    expect(screen.getByText(/no query got slower/i)).toBeInTheDocument();
  });
});
