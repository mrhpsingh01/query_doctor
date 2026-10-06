import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "@/lib/api";

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn().mockResolvedValue({ ok: status < 400, status, json: async () => body });
  vi.stubGlobal("fetch", fn);
  return fn;
}
afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("posts analyses as JSON to /api/analyses", async () => {
    const fetchFn = mockFetch(202, { id: 5, status: "queued" });
    const result = await api.createAnalysis({ sql: "SELECT 1", params: [], execute: false, project_id: 1 });
    expect(result.id).toBe(5);
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe("/api/analyses");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ sql: "SELECT 1", params: [], execute: false, project_id: 1 });
  });

  it("turns the API's structured 422 into an ApiError with code and message", async () => {
    mockFetch(422, { detail: { code: "not_select", message: "only SELECT statements are supported" } });
    const error = await api.getAnalysis(1).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("not_select");
    expect(error.message).toBe("only SELECT statements are supported");
    expect(error.status).toBe(422);
  });

  it("falls back to a readable message for plain-string and non-JSON errors", async () => {
    mockFetch(404, { detail: "analysis not found" });
    await expect(api.getAnalysis(9)).rejects.toThrow("analysis not found");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 502, json: async () => Promise.reject(new Error("x")) }));
    await expect(api.getAnalysis(9)).rejects.toThrow("Request failed (502)");
  });

  it("approves a benchmark with a run count and can cancel it", async () => {
    const fetchFn = mockFetch(202, { id: 9, status: "queued" });
    await api.approveBenchmark(4, 11, 5);
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe("/api/analyses/4/candidates/11/benchmark");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ runs: 5 });
    await api.cancelBenchmark(9);
    expect(fetchFn.mock.calls[1][0]).toBe("/api/benchmarks/9/cancel");
  });

  it("talks to the workload endpoints", async () => {
    const fetchFn = mockFetch(201, { id: 3 });
    await api.importWorkload(1);
    expect(fetchFn.mock.calls[0][0]).toBe("/api/projects/1/workload/import");
    expect(fetchFn.mock.calls[0][1].method).toBe("POST");
    await api.getWorkload(1);
    expect(fetchFn.mock.calls[1][0]).toBe("/api/projects/1/workload");
    await api.getWorkload(1, 4, 3);
    expect(fetchFn.mock.calls[2][0]).toBe("/api/projects/1/workload?snapshot=4&compare=3");
    await api.getWorkloadQuery(1, "abc");
    expect(fetchFn.mock.calls[3][0]).toBe("/api/projects/1/workload/queries/abc");
    await api.runDemoWorkload(1, "regress");
    expect(JSON.parse(fetchFn.mock.calls[4][1].body)).toEqual({ mode: "regress" });
    await api.listFingerprints(1);
    expect(fetchFn.mock.calls[5][0]).toBe("/api/projects/1/fingerprints");
  });

  it("builds report download URLs", () => {
    expect(api.reportUrl(4, "md")).toBe("/api/analyses/4/report?format=md");
    expect(api.reportUrl(4, "html")).toBe("/api/analyses/4/report?format=html");
  });
});
