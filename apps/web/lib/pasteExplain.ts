import type { Finding, ParsedPlan, PlanNode } from "./types";

type Obj = Record<string, unknown>;
const obj = (v: unknown): Obj => v !== null && typeof v === "object" && !Array.isArray(v) ? v as Obj : {};
const num = (v: unknown): number | null => typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null;
const str = (v: unknown): string | null => typeof v === "string" ? v : null;
export const UNVERIFIED = "Suggested (Unverified - Connect DB for HypoPG & Benchmark Validation)";
export interface PasteResult { plan: ParsedPlan; findings: Finding[]; suggestions: string[]; warnings: string[] }

/** Bounded, local-only parser. Never executes SQL or promotes pasted evidence. */
export function parseExplain(input: string): PasteResult {
  if (input.length > 1_000_000) throw new Error("Plan exceeds the 1 MB limit.");
  if (!input.trim()) throw new Error("Paste an EXPLAIN plan first.");
  let document: Obj;
  try {
    const value: unknown = JSON.parse(input);
    if (Array.isArray(value) && value.length !== 1) throw new Error("Paste exactly one plan at a time.");
    document = obj(Array.isArray(value) ? value[0] : value);
  } catch (error) {
    if (error instanceof Error && error.message === "Paste exactly one plan at a time.") throw error;
    if (/^[\s]*[\[{]/.test(input)) throw new Error("Invalid JSON. Paste the complete EXPLAIN output.");
    document = parseText(input);
  }
  const mongo = !!(document.executionStats || document.queryPlanner || document.stages);
  let count = 0;
  const warnings: string[] = [];
  function node(raw: Obj, parent: string | null, depth: number): PlanNode {
    if (++count > 400 || depth > 48) throw new Error("Plan exceeds 400 operators or 48 levels.");
    const id = `paste-${count}`;
    const lookup = obj(raw.$lookup);
    const cursor = obj(raw.$cursor);
    const cursorStats = obj(cursor.executionStats);
    const cursorPlanner = obj(cursor.queryPlanner);
    if (mongo && !raw.stage && raw.queryPlan) return node(obj(raw.queryPlan), parent, depth + 1);
    const stage = Object.keys(raw).find(k => k.startsWith("$"));
    const type = str(raw["Node Type"]) ?? str(raw.stage) ?? stage;
    if (!type) throw new Error("Unsupported plan operator: missing Node Type or stage.");
    let childrenRaw: unknown[] = Array.isArray(raw.Plans) ? raw.Plans : [];
    if (mongo) childrenRaw = [raw.inputStage, raw.outerStage, raw.innerStage, cursorStats.executionStages ?? cursorPlanner.winningPlan, ...(Array.isArray(raw.inputStages) ? raw.inputStages : [])].filter(Boolean);
    const children = childrenRaw.map(c => node(obj(c), id, depth + 1));
    const time = num(raw["Actual Total Time"]) ?? num(raw.executionTimeMillisEstimate) ?? num(raw.executionTimeMillis);
    const loops = num(raw["Actual Loops"]) ?? (mongo && time !== null ? 1 : null);
    const total = time === null ? null : time * (loops ?? 1);
    const rows = num(raw["Actual Rows"]) ?? num(raw.nReturned);
    const removed = num(raw["Rows Removed by Filter"]);
    const temp = num(raw["Temp Written Blocks"]);
    return {
      node_id: id, parent_id: parent, node_type: type,
      relation_name: str(raw["Relation Name"]) ?? str(lookup.from) ?? str(raw.collectionName),
      index_name: str(raw["Index Name"]) ?? str(raw.indexName), estimated_rows: num(raw["Plan Rows"]) ?? 0,
      actual_rows: rows, estimated_cost: num(raw["Total Cost"]) ?? 0, actual_time_ms: time, loops,
      rows_removed_by_filter: removed, shared_hit_blocks: num(raw["Shared Hit Blocks"]), shared_read_blocks: num(raw["Shared Read Blocks"]),
      temp_read_blocks: num(raw["Temp Read Blocks"]), temp_written_blocks: temp,
      filter_condition: str(raw.Filter), index_condition: str(raw["Index Cond"]), join_condition: str(raw["Hash Cond"]) ?? str(raw["Join Filter"]),
      plan_width: num(raw["Plan Width"]), sort_method: str(raw["Sort Method"]), hash_batches: num(raw["Hash Batches"]),
      output: Array.isArray(raw.Output) ? raw.Output.filter((x): x is string => typeof x === "string") : [],
      error_ratio: rows !== null && num(raw["Plan Rows"]) ? rows / Number(raw["Plan Rows"]) : null,
      filter_waste: removed !== null && rows !== null && removed + rows > 0 ? removed / (removed + rows) : null,
      total_time_ms: total, exclusive_time_ms: total === null ? null : Math.max(0, total - children.reduce((s, c) => s + (c.total_time_ms ?? 0), 0)),
      exclusive_temp_written_blocks: temp, exclusive_temp_read_blocks: num(raw["Temp Read Blocks"]),
      io_pressure: temp ? "temp" : num(raw["Shared Read Blocks"]) ? "disk" : "none",
      docs_examined: num(raw.totalDocsExamined) ?? num(raw.docsExamined), keys_examined: num(raw.totalKeysExamined) ?? num(raw.keysExamined), raw, children,
    };
  }
  let root: PlanNode;
  if (Array.isArray(document.stages) && document.stages.length) {
    if (document.stages.length > 48) throw new Error("Plan exceeds 48 aggregation stages.");
    // Aggregation times are cumulative estimates: chain stages rather than summing them.
    const stages = document.stages.map(s => node(obj(s), null, 0));
    for (let i = 1; i < stages.length; i++) {
      stages[i - 1].parent_id = stages[i].node_id;
      stages[i].children.push(stages[i - 1]);
      stages[i].exclusive_time_ms = stages[i].total_time_ms === null ? null : Math.max(0, stages[i].total_time_ms! - (stages[i - 1].total_time_ms ?? 0));
    }
    root = stages[stages.length - 1];
  } else {
    const stats = obj(document.executionStats), planner = obj(document.queryPlanner);
    const winning = obj(planner.winningPlan);
    root = node(obj(document.Plan ?? stats.executionStages ?? winning.queryPlan ?? planner.winningPlan), null, 0);
  }
  const all: PlanNode[] = [];
  const walk = (n: PlanNode) => { all.push(n); n.children.forEach(walk); };
  walk(root);
  const measured = all.filter(n => n.exclusive_time_ms !== null);
  const worst = [...(measured.length ? measured : all)].sort((a, b) => (b.exclusive_time_ms ?? b.estimated_cost) - (a.exclusive_time_ms ?? a.estimated_cost))[0];
  const suggestions = new Set<string>();
  const quote = (s: string) => `"${s.replaceAll('"', '""')}"`;
  for (const n of all) {
    if (/Seq Scan/.test(n.node_type) && n.relation_name) {
      const columns = [...(n.filter_condition ?? "").matchAll(/(?:\b\w+\.)?\b([a-zA-Z_][\w]*)\s*(?:=|>=|<=|>|<)/g)].map(m => m[1]);
      if (columns.length) suggestions.add(`Consider an index on ${quote(n.relation_name)} (${[...new Set(columns)].map(quote).join(", ")}); verify column order and selectivity against the schema.`);
      else suggestions.add(`Inspect selective predicates on ${quote(n.relation_name)} before choosing index columns.`);
    }
    if (/Sort|SORT/.test(n.node_type)) suggestions.add(`Consider a B-tree matching the filter and sort keys: ${JSON.stringify(n.raw["Sort Key"] ?? n.raw.sortPattern ?? "keys unavailable")}. Confirm ordering and expression compatibility.`);
    if (n.node_type === "$lookup") {
      const l = obj(n.raw.$lookup);
      if (str(l.from) && str(l.foreignField)) suggestions.add(`Consider db.getCollection(${JSON.stringify(l.from)}).createIndex(${JSON.stringify({ [String(l.foreignField)]: 1 })}); inspect existing indexes first.`);
      else suggestions.add("Inspect the $lookup foreign pipeline predicates and target indexes; correlated expressions may not be indexable.");
    }
    if (n.node_type === "COLLSCAN") suggestions.add("Consider an index on selective MongoDB filter fields; confirm the query and collection schema first.");
  }
  if (!measured.length) warnings.push("Planner estimates only: no measured bottleneck or latency is available.");
  if (mongo) warnings.push("MongoDB stage times may be cumulative estimates; HypoPG applies only to PostgreSQL.");
  const findings: Finding[] = [{ id: 1, rule_id: "paste_bottleneck", severity: "critical", title: measured.length ? "Largest reported operator time" : "Highest estimated cost", summary: "Heuristic highlight from supplied evidence; not independently verified.", evidence: {}, node_ids: [worst.node_id], confidence: 0.5, next_actions: [] }];
  return { plan: { root, engine: mongo ? "mongodb" : "postgres", executed: measured.length > 0, planning_time_ms: num(document["Planning Time"]), execution_time_ms: num(document["Execution Time"]) ?? num(obj(document.executionStats).executionTimeMillis) ?? root.total_time_ms, settings: {}, fingerprint: "unverified-paste" }, findings, suggestions: [...suggestions], warnings };
}

function parseText(text: string): Obj {
  const stack: { indent: number; raw: Obj }[] = [];
  let root: Obj | undefined;
  const document: Obj = {};
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^(\s*)(?:->\s*)?(.+?)\s+\(cost=([\d.e+-]+)\.\.([\d.e+-]+) rows=(\d+) width=(\d+)\)(?:\s+\(actual time=([\d.e+-]+)\.\.([\d.e+-]+) rows=(\d+) loops=(\d+)\))?/);
    if (match) {
      const [, space, title, , cost, rows, width, , time, actualRows, loops] = match;
      const parts = title.split(/\s+on\s+/);
      const raw: Obj = { "Node Type": parts[0].split(/\s+using\s+/)[0], "Relation Name": parts[1]?.split(/\s+/)[0], "Total Cost": Number(cost), "Plan Rows": Number(rows), "Plan Width": Number(width), Plans: [] };
      if (time !== undefined) Object.assign(raw, { "Actual Total Time": Number(time), "Actual Rows": Number(actualRows), "Actual Loops": Number(loops) });
      while (stack.length && stack[stack.length - 1].indent >= space.length) stack.pop();
      if (stack.length) (stack[stack.length - 1].raw.Plans as Obj[]).push(raw);
      else if (root) throw new Error("Paste one execution plan at a time.");
      else root = raw;
      stack.push({ indent: space.length, raw });
    } else {
      const metadata = line.trim().match(/^(Planning Time|Execution Time):\s*([\d.]+)/);
      if (metadata) document[metadata[1]] = Number(metadata[2]);
      const detail = line.trim().match(/^(Filter|Index Cond|Sort Key|Sort Method|Rows Removed by Filter):\s*(.*)$/);
      if (detail && stack.length) stack[stack.length - 1].raw[detail[1]] = detail[1] === "Rows Removed by Filter" ? Number(detail[2]) : detail[1] === "Sort Key" ? detail[2].split(", ") : detail[2];
      if (stack.length && line.trim().startsWith("Buffers:")) {
        const raw = stack[stack.length - 1].raw;
        for (const group of line.matchAll(/(shared|temp)\s+((?:(?:hit|read|written)=\d+\s*)+)/g)) {
          for (const metric of group[2].matchAll(/(hit|read|written)=(\d+)/g)) {
            const label = `${group[1] === "shared" ? "Shared" : "Temp"} ${metric[1][0].toUpperCase()}${metric[1].slice(1)} Blocks`;
            raw[label] = Number(metric[2]);
          }
        }
      }
    }
  }
  if (!root) throw new Error("Unsupported input. Paste PostgreSQL EXPLAIN JSON/text or MongoDB executionStats JSON.");
  return { ...document, Plan: root };
}
