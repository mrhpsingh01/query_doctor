# Rule catalog

Every finding carries: `rule_id`, `severity` (`info|warning|critical`), `title`, `summary`, `evidence` (the measurements
that triggered it), `node_ids` (where), `confidence` (0–1) and `next_actions` (what might help). A finding is a
*diagnosis*; whether a suggested change actually helps is established later (planner-validated, then
benchmark-validated). Thresholds live in `querydoctor.diagnosis.Thresholds` and can be overridden per analysis.

Row counts, times and blocks follow PostgreSQL's *per-loop* reporting; rules multiply by `loops` for totals.
When only `EXPLAIN` (no `ANALYZE`) was run, rules that need actuals stay silent or degrade to estimates with lower
confidence.

| Rule | Node types | Fires when | Severity | Default thresholds |
|---|---|---|---|---|
| `PG_SEQ_SCAN_FILTER_WASTE` | Seq Scan | rows visited (× loops) > min **and** filter waste > warn | `critical` if waste > critical (executed plans); `warning` otherwise or when estimated | `seq_scan_min_rows=100_000`, `waste_warn=0.80`, `waste_critical=0.95` |
| `PG_HIGH_FILTER_WASTE` | Index Scan, Index Only Scan, Bitmap Heap Scan, CTE/Subquery/Function/Values Scan | rows visited ≥ min **and** waste > warn (executed only) | `critical` if waste > critical, else `warning` | `high_filter_min_rows=10_000` |
| `PG_CARDINALITY_MISESTIMATE` | any | estimate/actual ratio ≥ warn and max(estimate, actual) ≥ min rows | `critical` if ratio ≥ critical, else `warning` | `misestimate_warn=10`, `misestimate_critical=100`, `misestimate_min_rows=1_000` |
| `PG_REPEATED_NESTED_LOOP` | any | `loops` ≥ min and the node's exclusive time is ≥ share of query time | `critical` if share ≥ critical, else `warning` | `loops_min=1_000`, `loop_time_share_warn=0.20`, `loop_time_share_critical=0.50` |
| `PG_SORT_SPILL` | Sort, Incremental Sort | sort method is external or temp blocks written > 0 | `critical` if the sort is ≥ `sort_spill_critical_share` of runtime, else `warning` | `sort_spill_critical_share=0.50` |

Definitions: *filter waste* = rows removed ÷ (rows returned + rows removed); *error ratio* =
max(actual ÷ estimate, estimate ÷ actual) with both floored at 1 row; *exclusive time* = node time × loops minus
the same for its children (never negative).
