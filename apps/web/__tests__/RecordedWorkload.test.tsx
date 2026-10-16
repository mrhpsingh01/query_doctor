import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FixQueueSummary } from "@/components/workload/FixQueueSummary";
import { RecordedWorkload } from "@/components/workload/RecordedWorkload";
import captured from "@/lib/workloadCaptured.json";
import type { FixQueue } from "@/lib/types";

describe("recorded workload (the Vercel showcase)", () => {
  it("renders the Ranked Fix Queue from the real capture with no network access at all", () => {
    const fetchSpy = vi.fn(() => { throw new Error("the showcase must not call the network"); });
    vi.stubGlobal("fetch", fetchSpy);
    render(<RecordedWorkload />);
    vi.unstubAllGlobals();
    expect(fetchSpy).not.toHaveBeenCalled();
    const queue = screen.getByRole("region", { name: "Ranked Fix Queue" });
    const fixes = within(queue).getAllByRole("listitem");
    expect(fixes).toHaveLength(captured.fix_queue.fixes.length);
    expect(fixes[0]).toHaveTextContent(captured.fix_queue.fixes[0].ddl); // ranked by time saved
    expect(queue).toHaveTextContent(/Benchmark-Validated indexes could recover approximately/);
    expect(queue).toHaveTextContent(/not CPU time/); // the honesty note travels with the number
  });

  it("says where the data came from and that the per-day figure is an extrapolation", () => {
    render(<RecordedWorkload />);
    expect(screen.getByText(/recorded from a real local run/i)).toBeInTheDocument();
    expect(screen.getByText(/assumes that traffic rate continues/i)).toBeInTheDocument();
  });

  it("offers no live actions and no links into routes that do not exist in the showcase", () => {
    const { container } = render(<RecordedWorkload />);
    expect(screen.queryByRole("button", { name: /import|generate|simulate|approve|refresh/i })).not.toBeInTheDocument();
    expect([...container.querySelectorAll("a")].map(a => a.getAttribute("href"))).not.toContainEqual(expect.stringMatching(/^\/(workload|analyses)\//));
    expect(screen.getByRole("link", { name: /run locally/i })).toBeInTheDocument();
  });

  it("ranks the busiest query shapes by total time", () => {
    render(<RecordedWorkload />);
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(captured.queries.length);
    expect(rows[0]).toHaveTextContent(/events/); // the biggest total-time consumer of the capture
  });
});

describe("FixQueueSummary", () => {
  const queue: FixQueue = {
    fixes: [
      { ddl: "CREATE INDEX ON orders (customer_id, created_at)", validation: "benchmark_validated", fingerprints: ["a", "b", "c"], analysis_ids: [7, 8, 9], saved_ms: 7_200_000, hours_per_day: 2 },
      { ddl: "CREATE INDEX ON payments (order_id)", validation: "benchmark_validated", fingerprints: ["d"], analysis_ids: [10], saved_ms: 1_800_000, hours_per_day: 0.5 },
    ],
    saved_hours_in_window: 2.5, hours_per_day: 2.5, note: "Estimated query execution time saved, not CPU time.",
  };
  it("shows one row per distinct index, how many query shapes it helps, and the total recovered", () => {
    render(<FixQueueSummary queue={queue} />);
    expect(screen.getByText(/2 Benchmark-Validated indexes could recover approximately 2\.5 hours of query execution time\/day/)).toBeInTheDocument();
    expect(screen.getByText(/Helps 3 query shapes/)).toBeInTheDocument();
    expect(screen.getByText(/Helps 1 query shape/)).toBeInTheDocument();
  });
  it("never invents a daily rate when the capture window is unknown", () => {
    render(<FixQueueSummary queue={{ ...queue, hours_per_day: null, fixes: queue.fixes.map(f => ({ ...f, hours_per_day: null })) }} />);
    expect(screen.getByText(/over the captured workload/)).toBeInTheDocument();
    expect(screen.getAllByText(/Daily rate unavailable/)).toHaveLength(2);
  });
  it("says so plainly when there is no benchmark evidence yet", () => {
    render(<FixQueueSummary queue={{ fixes: [], saved_hours_in_window: 0, hours_per_day: 0, note: "" }} />);
    expect(screen.getByText(/No qualifying benchmark evidence yet/)).toBeInTheDocument();
  });
});
