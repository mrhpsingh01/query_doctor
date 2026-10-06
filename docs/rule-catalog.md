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

## MongoDB rules

Ten rules read a MongoDB plan (`explain` with `executionStats`, or `queryPlanner` only), the query and the collection's size
and index list. MongoDB has no cost model, so every number is a measured work counter. Thresholds are the defaults of
`MongoThresholds`; planner-only plans keep only the rules that need no execution counters, at lower confidence.

| Rule | Fires when | Default | Severity |
|---|---|---|---|
| `MONGO_COLLSCAN` | a `COLLSCAN` with a filter reads at least this many documents (executed) or the collection is at least this large (planner-only) | 50,000 documents | critical when 95% of the documents read were discarded, otherwise warning |
| `MONGO_SCAN_AMPLIFICATION` | `docsExamined / max(nReturned, 1)` of a `FETCH` or `COLLSCAN` (executed only) | at least 20 per result and 1,000 documents | critical at 1,000 per result |
| `MONGO_KEY_AMPLIFICATION` | `keysExamined / max(nReturned, 1)` of an `IXSCAN` (executed only) | at least 50 per result and 1,000 keys | critical at 500 per result |
| `MONGO_BLOCKING_SORT` | an in-memory `SORT` over at least this many documents, or any sort that spilled to disk | 10,000 documents | critical when it spilled |
| `MONGO_INDEX_ORDER` | a compound index puts a range-filtered field before an equality-filtered field the query also uses; recommends equality, sort, range | n/a | warning |
| `MONGO_UNBOUNDED_AGG` | an aggregation reaches `$group`, `$bucket` or `$facet` with no `$match`, `$limit` or `$sample` before it over a large collection | 50,000 documents | warning |
| `MONGO_LARGE_LOOKUP` | a `$lookup` examined at least this many documents (executed) or targets an unindexed field of a large collection (planner-only) | 100,000 documents | critical when it used collection scans |
| `MONGO_UNSELECTIVE_INDEX` | an index scan returned at least this share of the collection (executed only) | 20% and 10,000 keys | warning |
| `MONGO_REDUNDANT_INDEX` | an index is a prefix of (or identical to) another; unique, partial and TTL indexes are exempt | n/a | info |
| `MONGO_STAGE_ORDER` | a `$match` follows `$group` (on the group key), `$sort`, or a `$project` that merely keeps its fields, so it could run first | n/a | warning after `$group`, otherwise info |

Scan amplification divides by `max(nReturned, 1)`, so a query that returns nothing is never a division by zero: 500,000
documents examined for 20 returned is 25,000 per result.
