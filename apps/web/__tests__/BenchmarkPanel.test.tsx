import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BenchmarkPanel } from "@/components/benchmarks/BenchmarkPanel";
import { chartData } from "@/components/benchmarks/LatencyChart";
import { formatBytes } from "@/lib/format";
import { benchmarkRun } from "./fixtures";

// Recharts needs real layout; the chart is exercised through its data function and its accessible table instead.
vi.mock("recharts", async () => {
  const actual = await vi.importActual<typeof import("recharts")>("recharts");
  return { ...actual, ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div> };
});

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
  closed = false;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    (this.listeners[type] ||= []).push(fn);
  }
  close() {
    this.closed = true;
  }
  emit(type: string) {
    this.listeners.progress?.forEach((fn) => fn({ data: JSON.stringify({ type }) } as MessageEvent));
  }
}
beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});
afterEach(() => vi.unstubAllGlobals());

describe("formatBytes", () => {
  it("uses binary units at a readable precision", () => {
    expect(formatBytes(86_000_000)).toBe("82 MB");
    expect(formatBytes(5_000_000)).toBe("4.8 MB");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(12)).toBe("12 B");
    expect(formatBytes(null)).toBe("n/a");
  });
});

describe("chartData", () => {
  it("lines the individual runs up side by side", () => {
    const rows = chartData(benchmarkRun().result!);
    expect(rows).toHaveLength(7);
    expect(rows[0]).toEqual({ run: 1, original: 700, indexed: 40 });
    expect(rows[6]).toEqual({ run: 7, original: 745, indexed: 41 });
  });

  it("tolerates sides with different run counts", () => {
    const result = benchmarkRun().result!;
    result.after_samples = result.after_samples.slice(0, 5);
    const rows = chartData(result);
    expect(rows).toHaveLength(7);
    expect(rows[6].indexed).toBeNull();
  });
});

describe("BenchmarkPanel: completed", () => {
  it("shows medians, p95, the improvement, index cost and plan identity", () => {
    render(<BenchmarkPanel run={benchmarkRun()} />);
    const table = screen.getByRole("table", { name: /summary/i });
    expect(within(table).getByText("742 ms")).toBeInTheDocument();
    expect(within(table).getByText("41 ms")).toBeInTheDocument();
    expect(within(table).getByText("811 ms")).toBeInTheDocument();
    expect(within(table).getByText("48 ms")).toBeInTheDocument();
    expect(screen.getByText("94.5% faster")).toHaveAttribute("data-outcome", "faster");
    expect(screen.getByText(/82 MB/)).toBeInTheDocument();
    expect(screen.getByText(/4\.2 s/)).toBeInTheDocument();
    expect(screen.getByText(/same output/i)).toBeInTheDocument();
    expect(screen.getByText(/isolated copy/i)).toBeInTheDocument();
  });

  it("lists every individual run in an accessible table", async () => {
    const user = userEvent.setup();
    render(<BenchmarkPanel run={benchmarkRun()} />);
    await user.click(screen.getByText(/all runs/i));
    const runs = screen.getByRole("table", { name: /individual runs/i });
    expect(within(runs).getAllByRole("row")).toHaveLength(8); // header + 7
    expect(within(runs).getByText("760 ms")).toBeInTheDocument();
  });

  it("shows a regression as slower, not as a win", () => {
    const run = benchmarkRun();
    run.result!.improvement_pct = -20;
    render(<BenchmarkPanel run={run} />);
    expect(screen.getByText("20% slower")).toHaveAttribute("data-outcome", "slower");
    expect(screen.getByText(/made this query slower/i)).toBeInTheDocument();
  });

  it("never claims a speed-up when the output differed; it says the results are not comparable", () => {
    const run = benchmarkRun();
    run.result!.checksum_match = false;
    run.result!.warnings = ["Output differs: the query returned different results"];
    render(<BenchmarkPanel run={run} />);
    const headline = screen.getByText(/not comparable/i);
    expect(headline).toHaveAttribute("data-outcome", "incomparable");
    expect(screen.queryByText(/% faster/)).not.toBeInTheDocument();
  });

  it("calls a difference inside the noise 'no measurable change', not a win", () => {
    const run = benchmarkRun();
    run.result!.improvement_pct = 0.04;
    render(<BenchmarkPanel run={run} />);
    expect(screen.getByText(/no measurable change/i)).toHaveAttribute("data-outcome", "same");
    expect(screen.queryByText(/% faster/)).not.toBeInTheDocument();
  });

  it("surfaces reliability warnings, and flags different output as blocking", () => {
    const run = benchmarkRun();
    run.result!.warnings = ["High variance in the before timings (CV 30%)", "Output differs: the query returned different results"];
    run.result!.checksum_match = false;
    render(<BenchmarkPanel run={run} />);
    const list = screen.getByRole("list", { name: /reliability warnings/i });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getByText(/output differs/i).closest("li")).toHaveAttribute("data-blocking", "true");
    expect(within(list).getByText(/high variance/i).closest("li")).toHaveAttribute("data-blocking", "false");
    expect(screen.queryByText(/same output/i)).not.toBeInTheDocument();
  });
});

describe("BenchmarkPanel: other states", () => {
  it("explains failures", () => {
    render(<BenchmarkPanel run={benchmarkRun({ status: "failed", result: null, error: "The index could not be created: boom" })} />);
    expect(screen.getByRole("alert")).toHaveTextContent("The index could not be created: boom");
  });

  it("says when a benchmark was cancelled", () => {
    render(<BenchmarkPanel run={benchmarkRun({ status: "cancelled", result: null })} />);
    expect(screen.getByText(/cancelled/i)).toBeInTheDocument();
  });

  it("follows a running benchmark's progress and offers a cancel", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(<BenchmarkPanel run={benchmarkRun({ status: "running", result: null, stage: "creating_sandbox" })} onCancel={onCancel} />);
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe("/api/benchmarks/3/events");
    expect(screen.getByText(/cloning the database/i)).toBeInTheDocument();
    act(() => source.emit("measuring_before"));
    expect(screen.getByText(/timing the original query/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /cancel benchmark/i }));
    expect(onCancel).toHaveBeenCalledWith(expect.objectContaining({ id: 3 }));
  });

  it("reports back once the stream ends so the page can refresh", () => {
    const onFinished = vi.fn();
    render(<BenchmarkPanel run={benchmarkRun({ status: "running", result: null })} onFinished={onFinished} />);
    act(() => FakeEventSource.instances[0].emit("benchmark_completed"));
    expect(onFinished).toHaveBeenCalledTimes(1);
  });

  it("does not open a stream for a finished run", () => {
    render(<BenchmarkPanel run={benchmarkRun()} />);
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("explains a queued benchmark", () => {
    render(<BenchmarkPanel run={benchmarkRun({ status: "queued", result: null, stage: null })} />);
    expect(screen.getByText(/waiting/i)).toBeInTheDocument();
  });
});
