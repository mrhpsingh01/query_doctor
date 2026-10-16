import { describe, expect, it } from "vitest";
import { showcaseFixtures } from "@/lib/showcaseFixtures";

const byId = (id: string) => showcaseFixtures.find(f => f.id === id)!;
const statuses = (id: string) => byId(id).ladder.map(r => r.status);

describe("verification ladder of the recorded showcases", () => {
  it("always lists the three rungs in order", () => {
    for (const f of showcaseFixtures) expect(f.ladder.map(r => r.label)).toEqual(["Suggested", "Planner-validated", "Benchmark-validated"]);
  });
  it("shows the earned rungs for the real wins", () => {
    expect(statuses("scan")).toEqual(["pass", "pass", "pass"]);
    expect(statuses("sort")).toEqual(["pass", "pass", "pass"]);
  });
  it("MongoDB has no planner rung (no hypothetical indexes), only measurement", () => {
    expect(statuses("lookup")).toEqual(["pass", "not-applicable", "pass"]);
    expect(byId("lookup").ladder[1].detail).toMatch(/no hypothetical indexes/i);
  });
  it("the false positive is Suggested and Planner-validated but FAILS the benchmark, with the measured reason", () => {
    expect(statuses("rejected")).toEqual(["pass", "pass", "fail"]);
    const failed = byId("rejected").ladder[2];
    expect(failed.detail).toMatch(/not used by the query's final plan/);
    expect(failed.detail).toMatch(/0\.2%/); // the measured improvement, taken from the capture
  });
  it("derives every rung from the capture, never from the title", () => {
    for (const f of showcaseFixtures) expect(f.ladder[2].status === "pass").toBe(f.capture.validated);
  });
});
