# Rule catalog

Every finding carries: `rule_id`, `severity` (`info|warning|critical`), `title`, `summary`, `evidence` (the measurements
that triggered it), `node_ids` (where), `confidence` (0–1) and `next_actions` (what might help). A finding is a
*diagnosis*; whether a suggested change actually helps is established later (planner-validated, then
benchmark-validated). Thresholds live in `querydoctor.diagnosis.Thresholds` and can be overridden per analysis.

Row counts and times follow PostgreSQL's *per-loop* reporting; rules multiply by `loops` for totals (below a `Gather`, elapsed time is divided by the number of processes). Buffer and temp-block counters include child nodes, so spill rules use the exclusive values.
When only `EXPLAIN` (no `ANALYZE`) was run, rules that need actuals stay silent or degrade to estimates with lower
confidence.

| Rule | Node types | Fires when | Severity | Default thresholds |
|---|---|---|---|---|
| `PG_SEQ_SCAN_FILTER_WASTE` | Seq Scan | rows visited (× loops) > min **and** filter waste > warn | `critical` if waste > critical (executed plans); `warning` otherwise or when estimated | `seq_scan_min_rows=100_000`, `waste_warn=0.80`, `waste_critical=0.95` |
| `PG_HIGH_FILTER_WASTE` | Index Scan, Index Only Scan, Bitmap Heap Scan, CTE/Subquery/Function/Values Scan | rows visited ≥ min **and** waste > warn (executed only) | `critical` if waste > critical, else `warning` | `high_filter_min_rows=10_000` |
| `PG_CARDINALITY_MISESTIMATE` | any | estimate/actual ratio ≥ warn and max(estimate, actual) ≥ min rows | `critical` if ratio ≥ critical, else `warning` | `misestimate_warn=10`, `misestimate_critical=100`, `misestimate_min_rows=1_000` |
| `PG_REPEATED_NESTED_LOOP` | any | `loops` ≥ min and the node's exclusive time is ≥ share of query time | `critical` if share ≥ critical, else `warning` | `loops_min=100`, `loop_time_share_warn=0.20`, `loop_time_share_critical=0.50` |
| `PG_SORT_SPILL` | Sort, Incremental Sort | sort method is external or the node's *own* (exclusive) temp blocks written > 0 | `critical` if the sort is ≥ `sort_spill_critical_share` of runtime, else `warning` | `sort_spill_critical_share=0.50` |
| `PG_HASH_SPILL` | Hash, Hash Join | Hash uses more than one batch or has exclusive temp blocks written (a Hash Join is reported only when its Hash child did not already explain the spill) | `critical` if the node is >= `hash_spill_critical_share` of runtime, else `warning` | `hash_spill_critical_share=0.50` |
| `PG_INDEX_FILTER_REMAINDER` | Index Scan, Index Only Scan, Bitmap Heap Scan | rows visited >= min, waste > threshold, and the leftover filter uses columns the index condition does not (supersedes `PG_HIGH_FILTER_WASTE` on that node) | `critical` if waste > `waste_critical`, else `warning` | `index_filter_min_rows=1_000`, `index_filter_waste=0.50` |
| `PG_FUNCTION_ON_COLUMN` | Seq Scan | the filter applies a function or cast to a column that leads an existing index (nothing is "blocked" if no such index exists) | `warning` | none |
| `PG_WIDE_RESULT` | root node | the query has `SELECT *` and the root row is at least `wide_result_bytes` wide or returns at least `wide_result_columns` columns | `warning` if width >= `wide_result_warn_bytes`, else `info` | `wide_result_bytes=200`, `wide_result_warn_bytes=400`, `wide_result_columns=15` |
| `PG_DUPLICATE_SUBTREE` | any | two or more structurally identical subtrees read the same table the same way and cost at least the minimum (reported once, at the largest matching subtree) | `warning` if they account for >= `duplicate_time_share_warn` of runtime, else `info` | `duplicate_min_cost=1_000`, `duplicate_time_share_warn=0.20` |

Definitions: *filter waste* = rows removed ÷ (rows returned + rows removed); *error ratio* =
max(actual ÷ estimate, estimate ÷ actual) with both floored at 1 row; *exclusive time* = node time × loops minus
the same for its children (never negative).

## Scale-aware default in the API

The sample database's hottest table (`orders`) has exactly 100,000 rows at the `small` scale, which would sit on the
default `seq_scan_min_rows=100_000` boundary. The API therefore lowers the floor to 50,000 when `QD_SCALE=small`
(override with `QD_SEQ_SCAN_MIN_ROWS`). The library default is unchanged.
