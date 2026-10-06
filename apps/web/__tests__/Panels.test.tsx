import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Panels } from "@/components/workload/Panels";
import { dashboard, regression, workloadQuery } from "./fixtures";

describe("Panels", () => {
  it("shows the seven panels in the order of the guide", () => {
    const d = dashboard();
    render(<Panels panels={d.panels} queries={d.queries} />);
    expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
      "Highest total time",
      "Slowest queries (estimated p95)",
      "Most frequent",
      "Highest I/O",
      "Most unstable",
      "Largest regression",
      "Missing benchmark validation",
    ]);
  });

  it("lists each panel's queries in the order given with the metric that panel is about", () => {
    const d = dashboard();
    render(<Panels panels={d.panels} queries={d.queries} />);
    const slowest = screen.getByRole("heading", { name: /slowest queries/i }).closest("section")!;
    const items = within(slowest).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("SELECT * FROM events");
    expect(items[0]).toHaveTextContent("400 ms");
    expect(items[0]).toHaveTextContent(/est/i);
    const frequent = screen.getByRole("heading", { name: /most frequent/i }).closest("section")!;
    expect(within(frequent).getAllByRole("listitem")[0]).toHaveTextContent("1,500 calls");
    expect(within(frequent).getAllByRole("link")[0]).toHaveAttribute("href", "/workload/8ca0e74b3bc8a91d");
  });

  it("shows regressions as how many times slower, using the query text from the ranked list", () => {
    const d = dashboard();
    d.panels.largest_regression = [regression()];
    render(<Panels panels={d.panels} queries={d.queries} />);
    const section = screen.getByRole("heading", { name: /largest regression/i }).closest("section")!;
    expect(section).toHaveTextContent("5× slower");
    expect(section).toHaveTextContent("FROM orders WHERE customer_id");
  });

  it("explains an empty panel instead of leaving it blank", () => {
    const d = dashboard();
    render(<Panels panels={d.panels} queries={d.queries} />);
    const io = screen.getByRole("heading", { name: /highest i\/o/i }).closest("section")!;
    expect(io).toHaveTextContent(/no query/i);
    expect(within(io).queryByRole("listitem")).not.toBeInTheDocument();
  });

  it("shows the I/O panel's blocks and the instability as variation", () => {
    const d = dashboard();
    d.panels.highest_io = [workloadQuery({ shared_read_blocks: 900, temp_blocks: 100 })];
    d.panels.most_unstable = [workloadQuery({ mean_ms: 10, stddev_ms: 25 })];
    render(<Panels panels={d.panels} queries={d.queries} />);
    expect(screen.getByRole("heading", { name: /highest i\/o/i }).closest("section")).toHaveTextContent("1,000 blocks");
    expect(screen.getByRole("heading", { name: /most unstable/i }).closest("section")).toHaveTextContent("std dev 2.5× the mean");
  });

  it("uses the singular for exactly one block", () => {
    const d = dashboard();
    d.panels.highest_io = [workloadQuery({ shared_read_blocks: 1, temp_blocks: 0 })];
    render(<Panels panels={d.panels} queries={d.queries} />);
    expect(screen.getByText("1 block")).toBeInTheDocument();
  });
});
