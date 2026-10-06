import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WorkloadDashboard } from "@/components/workload/WorkloadDashboard";
import { ApiError } from "@/lib/api";
import { dashboard, regression } from "./fixtures";

const mocks = vi.hoisted(() => ({ getWorkload: vi.fn(), importWorkload: vi.fn(), runDemoWorkload: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<typeof import("@/lib/api")>()), api: mocks }));

const empty = () =>
  dashboard({
    snapshot: null,
    queries: [],
    regressions: [],
    panels: { ...dashboard().panels, highest_total_time: [], slowest_p95: [], most_frequent: [], missing_validation: [] },
  });

beforeEach(() => {
  Object.values(mocks).forEach((m) => m.mockReset());
});

describe("WorkloadDashboard", () => {
  it("shows an instructive empty state before anything was imported", async () => {
    mocks.getWorkload.mockResolvedValue(empty());
    render(<WorkloadDashboard projectId={1} />);
    expect(await screen.findByText(/no workload imported yet/i)).toBeInTheDocument();
    expect(screen.getAllByText(/pg_stat_statements/i).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /import statistics/i })).toBeEnabled();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows the ranked table, the panels, and what the snapshot covers", async () => {
    mocks.getWorkload.mockResolvedValue(dashboard());
    render(<WorkloadDashboard projectId={1} />);
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Highest total time" })).toBeInTheDocument();
    expect(screen.getByText(/2 queries/i)).toBeInTheDocument();
    expect(screen.getByText(/4 skipped/i)).toBeInTheDocument();
  });

  it("imports through the API and reloads the dashboard", async () => {
    const user = userEvent.setup();
    mocks.getWorkload.mockResolvedValueOnce(empty()).mockResolvedValue(dashboard());
    mocks.importWorkload.mockResolvedValue({ id: 2 });
    render(<WorkloadDashboard projectId={7} />);
    await user.click(await screen.findByRole("button", { name: /import statistics/i }));
    expect(mocks.importWorkload).toHaveBeenCalledWith(7, "postgres");
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(mocks.getWorkload).toHaveBeenCalledTimes(2);
  });

  it("shows the server's message when the import fails and keeps the page usable", async () => {
    const user = userEvent.setup();
    mocks.getWorkload.mockResolvedValue(empty());
    mocks.importWorkload.mockRejectedValue(new ApiError("Could not read pg_stat_statements from the target database.", 502));
    render(<WorkloadDashboard projectId={1} />);
    await user.click(await screen.findByRole("button", { name: /import statistics/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not read pg_stat_statements");
    expect(screen.getByRole("button", { name: /import statistics/i })).toBeEnabled();
  });

  it("starts demo traffic and says to import again afterwards", async () => {
    const user = userEvent.setup();
    mocks.getWorkload.mockResolvedValue(dashboard());
    mocks.runDemoWorkload.mockResolvedValue({ status: "queued", mode: "regress" });
    render(<WorkloadDashboard projectId={1} />);
    await user.click(await screen.findByRole("button", { name: /simulate a regression/i }));
    expect(mocks.runDemoWorkload).toHaveBeenCalledWith(1, "regress", "postgres");
    expect(await screen.findByRole("status")).toHaveTextContent(/import again/i);
  });

  it("lists regressions found against the previous snapshot", async () => {
    mocks.getWorkload.mockResolvedValue(dashboard({ compare_to: dashboard().snapshot, regressions: [regression()] }));
    render(<WorkloadDashboard projectId={1} />);
    const heading = await screen.findByRole("heading", { name: /^regressions$/i });
    expect(heading.closest("section")).toHaveTextContent("was 5 ms, now 25 ms (5× slower)");
  });

  describe("engine filter", () => {
    const mongoSnapshot = () => ({ ...dashboard().snapshot!, engine: "mongodb", source: "profiler" });
    const mongoDashboard = () =>
      dashboard({
        snapshot: mongoSnapshot(),
        queries: dashboard().queries.map((q) => ({ ...q, normalized_sql: '{"find":"orders","filter":{"customer_id":"?"}}', sample_sql: '{"find": "orders", "filter": {"customer_id": 77}}' })),
      });

    it("shows PostgreSQL first and loads the dashboard of the chosen database", async () => {
      const user = userEvent.setup();
      mocks.getWorkload.mockResolvedValue(dashboard());
      render(<WorkloadDashboard projectId={1} />);
      expect(await screen.findByRole("table")).toBeInTheDocument();
      expect(screen.getByRole("radio", { name: "PostgreSQL" })).toBeChecked();
      expect(mocks.getWorkload).toHaveBeenLastCalledWith(1, undefined, undefined, "postgres");
      mocks.getWorkload.mockResolvedValue(mongoDashboard());
      await user.click(screen.getByRole("radio", { name: "MongoDB" }));
      expect((await screen.findAllByRole("link", { name: /customer_id/ })).length).toBeGreaterThan(0);
      expect(mocks.getWorkload).toHaveBeenLastCalledWith(1, undefined, undefined, "mongodb");
      expect(screen.getByRole("radio", { name: "MongoDB" })).toBeChecked();
    });

    it("explains the profiler in the empty state and imports, and generates traffic, for MongoDB", async () => {
      const user = userEvent.setup();
      mocks.getWorkload.mockResolvedValue(empty());
      mocks.importWorkload.mockResolvedValue({ id: 3 });
      mocks.runDemoWorkload.mockResolvedValue({ status: "queued", mode: "baseline", engine: "mongodb" });
      render(<WorkloadDashboard projectId={4} />);
      await user.click(await screen.findByRole("radio", { name: "MongoDB" }));
      expect(await screen.findByText(/no workload imported yet/i)).toBeInTheDocument();
      expect(screen.getAllByText(/profiler/i).length).toBeGreaterThan(0);
      expect(screen.queryByText(/pg_stat_statements/i)).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: /import profiler data/i }));
      expect(mocks.importWorkload).toHaveBeenCalledWith(4, "mongodb");
      await user.click(screen.getByRole("button", { name: /generate demo traffic/i }));
      expect(mocks.runDemoWorkload).toHaveBeenCalledWith(4, "baseline", "mongodb");
    });

    it("opens the analysis page in MongoDB mode from a MongoDB workload row", async () => {
      const user = userEvent.setup();
      mocks.getWorkload.mockResolvedValue(dashboard());
      render(<WorkloadDashboard projectId={1} />);
      await screen.findByRole("table");
      mocks.getWorkload.mockResolvedValue(mongoDashboard());
      await user.click(screen.getByRole("radio", { name: "MongoDB" }));
      const links = await screen.findAllByRole("link", { name: /analyze/i });
      expect(links[0].getAttribute("href")).toContain("engine=mongodb");
      expect(links[0].getAttribute("href")).toContain(encodeURIComponent('{"find": "orders", "filter": {"customer_id": 77}}'));
    });
  });
});
