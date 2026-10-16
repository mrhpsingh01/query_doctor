import { describe, expect, it } from "vitest";
import { parseExplain } from "@/lib/pasteExplain";
import { showcaseFixtures } from "@/lib/showcaseFixtures";
import fs from "node:fs";
import path from "node:path";
describe("local EXPLAIN parser", () => {
  it("parses all checked-in PostgreSQL and MongoDB captures", () => {
    for (const folder of ["postgres_plans", "mongo_plans"]) {
      const dir = path.resolve("../../fixtures", folder);
      for (const file of fs.readdirSync(dir)) expect(parseExplain(fs.readFileSync(path.join(dir, file), "utf8")).plan.root.node_type, file).toBeTruthy();
    }
  });
  it("preserves text hierarchy and loops", () => {
    const r = parseExplain(`Nested Loop  (cost=0.00..20.00 rows=2 width=8) (actual time=0.1..5.0 rows=2 loops=1)
  ->  Seq Scan on orders  (cost=0.00..10.00 rows=2 width=8) (actual time=0.1..1.0 rows=2 loops=2)
        Filter: (customer_id = 3)
Execution Time: 5.1 ms`);
    expect(r.plan.root.children[0].total_time_ms).toBe(2);
    expect(r.plan.root.exclusive_time_ms).toBe(3);
    expect(r.suggestions[0]).toContain('"customer_id"');
  });
  it("keeps estimates unexecuted", () => expect(parseExplain('{"Plan":{"Node Type":"Seq Scan"}}').plan.executed).toBe(false));
  it("rejects malformed and excessive inputs", () => {
    for (const input of ["", "SELECT 1", "{bad", "{}", "x".repeat(1_000_001)]) expect(() => parseExplain(input)).toThrow();
    let plan: object = { "Node Type": "Result" };
    for (let i = 0; i < 50; i++) plan = { "Node Type": "Result", Plans: [plan] };
    expect(() => parseExplain(JSON.stringify({ Plan: plan }))).toThrow(/levels/);
  });
  it("labels examples honestly", () => {
    expect(showcaseFixtures).toHaveLength(4);
    for (const item of showcaseFixtures) expect(item.provenance).toContain("using the QueryDoctor pipeline");
    expect(showcaseFixtures[3].verdict).toContain("Benchmark rejected");
  });
});


describe("paste input boundaries", () => {
  it("refuses multiple plans instead of silently dropping all but the first", () => {
    expect(() => parseExplain('[{"Plan":{"Node Type":"Result"}},{"Plan":{"Node Type":"Seq Scan"}}]')).toThrow(/exactly one plan/);
  });
  it("handles a nested MongoDB queryPlan wrapper", () => {
    const result = parseExplain(JSON.stringify({queryPlanner:{winningPlan:{stage:"FETCH",inputStage:{queryPlan:{stage:"IXSCAN",indexName:"customer_id_1"}}}}}));
    expect(result.plan.root.children[0].node_type).toBe("IXSCAN");
  });
  it("preserves standard text BUFFERS details without inventing execution", () => {
    const result = parseExplain(`Sort  (cost=10.00..20.00 rows=2 width=8) (actual time=1.0..5.0 rows=2 loops=1)
  Sort Method: external merge Disk: 400kB
  Buffers: shared hit=2 read=3, temp read=4 written=5
  ->  Seq Scan on orders  (cost=0.00..10.00 rows=2 width=8) (actual time=0.1..1.0 rows=2 loops=1)`);
    expect(result.plan.root.temp_written_blocks).toBe(5);
    expect(result.plan.root.shared_read_blocks).toBe(3);
    expect(result.plan.root.io_pressure).toBe("temp");
  });
});
