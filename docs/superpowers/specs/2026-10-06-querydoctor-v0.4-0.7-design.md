# QueryDoctor v0.4–v0.7 Design (Phases 7–12)

Date: 2026-10-06
Status: Design approved in conversation ("continue"); built slice by slice on top of v0.3
Builds on: `2026-10-05-querydoctor-v0.1-0.3-design.md` (unchanged contracts are not repeated here)

## 1. Scope and success criteria

Four slices, each with its own tests, verification gate and git tag:

| Slice | Phase | Exit criterion (from the build guide, made testable) |
|---|---|---|
| **v0.4 Workload intelligence** | 7 | The dashboard says which query to optimize first, from imported `pg_stat_statements`, and shows regressions and per-fingerprint history. |
| **v0.5 ML ranker** | 8 | A benchmark-labelled dataset exists; heuristic, logistic, LightGBM and ranker models are evaluated with group-based splits; the evaluation is shown in the UI; predictions are labelled as predictions and never change evidence levels; the system falls back to the heuristic safely. *Whether ML beats the heuristic is measured and reported honestly.* |
| **v0.6 Grounded explanations + hardening** | 9, 11, 12 | Explanations come from structured facts, every number is validated against them, and removing the LLM removes only the prose. Custom PostgreSQL connections exist behind an off-by-default flag with an SSRF guard. Chaos tests pass. |
| **v0.7 MongoDB** | 10 | PostgreSQL and MongoDB analyses appear in the same UI with database-specific evidence. |

Non-goals: Auth.js/Clerk and OpenTelemetry/Sentry (not in the remaining-phase list; a single-user local setup does not need them), LangGraph (the four-step flow is a plain state machine that keeps the guide's node names), benchmarking customer databases (benchmarks stay sample-only because they need a clone), automatic changes of any kind.

## 2. Cross-cutting decisions

* **New packages inside `querydoctor`:** `workload/`, `ranking/`, `explain/`, `netguard/`, `mongo/` and `adapters/`. Pure logic stays pure and fixture-tested; only thin modules touch a database or HTTP.
* **New dependencies:** `scikit-learn`, `lightgbm`, `pandas`, `joblib` (ranking); `pymongo` (MongoDB); `httpx` becomes a runtime dependency (Ollama client).
* **Ports (all bound to 127.0.0.1):** `mongo` 27017. Ollama is the host's own server at `127.0.0.1:11434`; containers reach it as `host.docker.internal:11434`.
* **Host addresses use `127.0.0.1`**, never `localhost` (IPv6 stalls against loopback-only ports).
* **Engine:** `analyses` and `query_snapshots` gain `engine` (`postgres` | `mongodb`, default `postgres`). The `sql` field holds the query text for both engines (JSON text for MongoDB). A second sample project "Sample shop (MongoDB)" is seeded.
* **Evidence levels are unchanged** (`suggested`, `planner_validated`, `benchmark_validated`). Nothing in this spec (ML, explanations) may raise one. MongoDB has no hypothetical-index equivalent, so Mongo candidates go from `suggested` straight to `benchmark_validated`.
* **Migrations:** one Alembic revision per slice (0004 workload, 0005 ranking, 0006 explanations/connections, 0007 engine).
* **Testing discipline is unchanged:** test first, watch it fail, commit per task, fresh-context review at the end of the whole build, integration tests against the live Docker stack. A `chaos` marker (excluded from the default run, run explicitly) holds disruptive tests.

## 3. Slice v0.4 — Workload intelligence

### 3.1 Data

`workload_snapshots(id, project_id, engine, source, taken_at, query_count, skipped_count)` and
`workload_queries(id, snapshot_id, fingerprint, normalized_sql, calls, total_ms, mean_ms, stddev_ms, min_ms, max_ms, rows, shared_hit_blocks, shared_read_blocks, temp_blocks, p95_est_ms)` with `UNIQUE(snapshot_id, fingerprint)`.

### 3.2 Import (`querydoctor.workload`)

* `read_statements(conn, limit=500)` reads `pg_stat_statements` for the current database via the read-only role (granted `pg_read_all_stats` by the seed job). It selects `queryid, query, calls, total_exec_time, mean_exec_time, stddev_exec_time, min/max_exec_time, rows, shared_blks_hit/read, temp_blks_read+written`.
* Each statement is run through `validate_query`; non-`SELECT` or unparsable statements, and statements touching no table, are skipped and counted. The validator's fingerprint of pgss's `$n`-normalized text equals the fingerprint of the same query with literals, which is what links workload rows to QueryDoctor analyses.
* Rows with the same fingerprint (different `queryid`/user/database) are merged: calls and totals add, mean = total/calls, standard deviation by pooled variance.
* pg_stat_statements has no percentiles. `p95_est_ms = mean + 1.645·stddev` is stored and **labelled an estimate** everywhere. Measured p95 comes only from QueryDoctor's own benchmarks.

### 3.3 Analysis (pure functions)

* **Priority rank:** `total_ms = mean × calls` and its share of the snapshot total; this is the default order ("optimize this first").
* **Panels:** highest total time, slowest (estimated) p95, most frequent, highest I/O (`shared_read + temp`), most unstable (CV = stddev/mean, calls ≥ 20), largest regression, and *missing benchmark validation* (fingerprint has no analysis whose candidate reached `benchmark_validated`; computed by the API from the metadata DB).
* **Regression detection** compares two snapshots by fingerprint. pgss counters are cumulative, so the *interval* mean is `(total_b − total_a)/(calls_b − calls_a)` and the baseline is snapshot A's cumulative mean. A regression needs interval calls ≥ 20 and `interval_mean / baseline_mean ≥ 1.5` (both thresholds configurable).
* **Plan changes:** across analyses of one fingerprint, a change of `plan_fingerprint` is reported with the before/after operators.
* **History by fingerprint:** first/last observed, number of analyses, mean and measured p95 of executed analyses, plan-change count, best evidence level reached.

### 3.4 Lab workload

`lab.workload` runs the catalog queries with varied parameters and weights (Zipf-weighted customers, random dates/statuses), plus a few cheap high-frequency queries (primary-key lookups, tens of thousands of calls), so total-time ranking visibly differs from per-call latency, which is the guide's point. It calls `pg_stat_statements_reset()` with the admin role before a baseline run. A `degrade` mode re-runs one fingerprint with heavier parameters to simulate a regression. The seed job runs a baseline workload after seeding; the UI can trigger `baseline` and `regress` runs (a worker task using the read-only role; no reset).

### 3.5 API and UI

* `POST /api/projects/{id}/workload/import` (inline, fast) -> snapshot
* `GET  /api/projects/{id}/workload?snapshot=&compare=` -> snapshot, ranked queries with share, panels, regressions
* `GET  /api/projects/{id}/workload/queries/{fingerprint}` -> trend across snapshots, analyses history, plan changes, sample parameters when the fingerprint matches a catalog query
* `POST /api/projects/{id}/workload/demo {mode: baseline|regress}`
* `GET  /api/projects/{id}/fingerprints` -> history grouped by fingerprint
* `/workload` page: import button, the seven panels, a ranked table with share bars, a regressions list; `/workload/[fingerprint]` detail; a link from each query to New analysis pre-filled. The History page gains the per-fingerprint table.

### 3.6 Tests

Unit: statement merging and pooled variance, ranking, panels, regression thresholds, plan-change detection (fixtures). Integration: lab workload -> import -> ranking (a 50 ms query called 50,000 times outranks a 5 s query called once), regression demo detected, API and UI journeys.

## 4. Slice v0.5 — ML recommendation ranker

### 4.1 Features (`querydoctor.ranking.features`)

`FEATURE_NAMES` is a fixed, versioned list (~45 features); `extract_features(...)` returns `dict[str, float]`. All features are available from **planner-only** evidence, so the ranker works without executing the query. (The guide's "actual execution time" and "loop count" original-plan features are therefore replaced by planner estimates; recorded as a ruling.)

* **Query:** tables, joins, equality/range/join/order/group column counts, output columns, `SELECT *`, subqueries, expression predicates.
* **Table (candidate's table):** log row count, relation size, column count, existing-index count, write ratio (`pg_stat_user_tables`), and for the key columns the mean `n_distinct` fraction, null fraction and |correlation| from `pg_stats`.
* **Original plan:** node counts by type, log estimated cost, log estimated root rows, depth, node count, estimated sequential-scan rows.
* **Candidate:** key/include column counts, partial/expression flags, DESC count, estimated key width, overlap with existing indexes, leading-column role (equality/join/range/order), equality coverage, range coverage, whether the sort is served, output coverage.
* **Plan difference (HypoPG plan vs original):** cost, rows, risk and operator terms, planner score, `uses_candidate`, removed sequential scans, added index scans, sort removed, join algorithm changed, depth and node deltas.

`collect_schema` gains optional extras (relation size, write ratio, per-column `pg_stats`) without changing existing callers.

### 4.2 Dataset

* Data variants (each a template database on the bench instance with `hypopg` available through `template1`): `tiny` (1k customers / 10k orders), `small`, `medium` (30k / 300k), `skew_low` (Zipf 0.5) and `skew_high` (1.1). The generator takes a variant name; `COUNTS`/`ZIPF` tables extend the existing `small`/`full`.
* Roughly 30 query templates over the shop schema, with sampled parameters (heavy and light customers, dates, statuses, countries, categories). Candidates are the generator's own (pruned) plus deliberately useless decoys (index on an unrelated column, wrong column order, a lone leading column of a good composite).
* Each *query instance* (template, variant, parameters) is planned once, scored with HypoPG in a per-variant explore clone, then **benchmarked with one shared baseline** and one real index at a time in a single clone (`measure_candidates`: baseline once, then create index -> measure -> drop). Labels: `log_speedup = ln(before_median/after_median)`, `improvement_pct`, `useful = improvement >= 20%`, `regression = speedup < 0.9`, `index_size_mb`, build ms.
* `group_id = query fingerprint` (a template across all variants and parameters shares a group, so nothing leaks between train and test); `query_id = (fingerprint, variant, parameter hash)` groups candidates for ranking metrics.
* Output: `datasets/ranker-v1.jsonl` (committed) with the feature vector, labels and metadata. Generation takes on the order of an hour and runs in the background.

### 4.3 Models and evaluation (`querydoctor.ranking.train`)

1. **Heuristic:** the existing planner score.
2. **Logistic regression** (standardized features) for `useful`.
3. **LightGBM** regressor (`log_speedup`), classifier (`useful`), a small regressor for `log(index_size)`, and a classifier for `regression`.
4. **LightGBM LambdaRank** with graded relevance bands from the speedup, grouped by `query_id`.

Evaluation uses group-based splits only: a held-out set of groups (about 25–30%) plus `GroupKFold(5)` for stability. Reported for every model: MAE of log speedup, precision@3 and NDCG@3 over queries, the share of top-ranked candidates that really improve, and calibration (Brier, ECE and reliability-curve points) for probabilities. Probabilities are calibrated with isotonic regression fitted on out-of-fold predictions. The deployed ranking score is the model with the best held-out NDCG@3; `use_for_ranking` is true only if it beats the heuristic on held-out NDCG@3 **and** precision@3, otherwise the heuristic keeps ordering and ML output is shown for information. Artifacts: `models/ranker.joblib` and `models/ranker.meta.json` (feature names, per-model metrics, feature importances, calibration points, data hash, versions, `use_for_ranking`).

### 4.3a Explanations of predictions

LightGBM's native `pred_contrib` gives per-feature contributions; the top contributions are mapped to plain phrases through a descriptor table ("removes a large sequential scan", "covers the equality and range predicates"). Never "the AI thinks".

### 4.4 In the product

* After candidate scoring the pipeline extracts features for each kept candidate and, if a valid model is loaded (feature names match), stores a `model_predictions` row: expected speedup, probability of >=20% improvement (calibrated), expected index size, probability of regression, rank score, top contributions. A missing, invalid or failing model changes nothing (heuristic fallback).
* The UI shows "Predicted: ~8x faster, 93% likely to improve" in a visibly separate **Prediction** block with the contributions; it never alters the evidence badge. If `use_for_ranking`, planner-validated candidates are ordered by the model; otherwise by planner score.
* `user_feedback(analysis_id, candidate_id, helpful, comment, created_at)` with thumbs in the UI.
* **Monitoring:** when a benchmark completes for a candidate that had a prediction, a `model_outcomes` row records actual vs predicted. `GET /api/model/monitor` reports n, MAE, precision of positive predictions, calibration bins and a drift flag (MAE > 1.5x the training MAE with n >= 20).
* `/models` page: per-model metric table and chart (heuristic vs learned), feature importances, calibration curve, dataset statistics, `use_for_ranking` with the reason, and the monitoring card. `GET /api/model` serves the metadata.

### 4.5 Tests

Unit: every feature group on fixtures, feature-name stability, group-split leakage test (no group in both sides), metric functions (precision@k, NDCG@k, ECE) against hand-computed values, descriptor mapping, predictor fallback (missing file, name mismatch, exception). Integration: a small dataset run end to end (query -> candidates -> HypoPG -> shared-baseline benchmark -> row), pipeline stores predictions, outcome recording on benchmark completion.

## 5. Slice v0.6 — Grounded explanations, hardening, chaos

### 5.1 Grounded explanations (`querydoctor.explain`)

Workflow nodes (a plain state machine): `build_evidence -> draft -> validate -> review`.

* **Evidence package:** `build_evidence(analysis_dict, kind)` yields `Fact(id, label, value, unit, text)` entries (F1..Fn) drawn only from computed data: tables, timings, slowest operator and share, each finding's evidence, candidate DDL/score/plan change/prediction, benchmark medians/p95/improvement/index size/build time/runs, warnings.
* **Kinds:** `plan` (explain to a junior developer), `findings` (summary), `tradeoffs`, `next_experiment`, `pr_comment`.
* **Template explainer:** deterministic composition; each sentence carries the fact ids it used. Always available.
* **Ollama explainer:** local HTTP (`/api/chat`, non-streaming, low temperature). The system prompt forbids new numbers, SQL generation and safety claims and requires citations like `[F3]`; the evidence JSON is the only context. Model from `QD_OLLAMA_MODEL`; empty means disabled. Unreachable/slow/model-missing -> `ExplainerUnavailable`.
* **Claim validator:** extracts every standalone number (with `%`, `x`, `ms`, `s`, `MB`, `k`/`M` suffixes and thousands separators; digits inside identifiers like `customer_id`, `t1` or `$1` are ignored) and requires a match to a fact value or an exact derived form (percent, ratio, rounding at the displayed precision, within 1%). Sentences with unsupported numbers are removed; if nothing survives, more than half the sentences were removed, or any `CREATE`/`DROP`/`ALTER` appears, the draft is rejected and the template is used with the reason recorded.
* **Persistence:** `explanations(id, analysis_id, kind, status, source, text, citations, dropped_sentences, fallback_reason, reviewed, created_at)`. `POST /api/analyses/{id}/explanations {kind}` queues a worker task (instant for the template), `GET /api/explanations/{id}` is polled by the UI, `POST /api/explanations/{id}/review {accepted}` records the human decision ("human reviews action"). Nothing an explanation says is executed.
* **UI:** an "Explain" panel on the analysis page with the five kinds, a source badge ("Template" or "Ollama · model, validated"), a collapsible "Facts used" list and a copy button for the PR comment. With no LLM configured everything still works.

### 5.2 Custom PostgreSQL connections (flag `QD_ALLOW_CUSTOM_CONNECTIONS`, default off)

* **Registry:** `POST /api/projects/{id}/connections`, `GET`, `DELETE /api/connections/{id}`. Passwords are Fernet-encrypted; deleting a connection deletes the secret ("credential deletion on request"). Audit events `connection_created`, `connection_deleted`, `connection_used`. `sslmode` defaults to `prefer`, `require` selectable.
* **SSRF guard (`netguard`):** resolve the host to all A/AAAA records and reject if any address is loopback, link-local (including `169.254.169.254` and the IPv6 equivalents), private (10/8, 172.16/12, 192.168/16, fc00::/7), CGNAT (100.64/10), multicast, reserved or unspecified, or an IPv4-mapped IPv6 form of those, or a `localhost`-style name; require the port to be in an allowlist (default 5432). Private ranges are allowed only with `QD_ALLOW_PRIVATE_NETWORKS=true`. **DNS rebinding:** the connection is made to the *resolved IP* (`hostaddr`, keeping the hostname for TLS verification), so a second resolution can never differ. The sample hosts stay allowlisted by name.
* **Use:** analyses accept `connection_id`; the pipeline connects with the decrypted credentials through the same guarded (read-only, non-superuser) session. HypoPG unavailable on the remote database is reported ("index testing is unavailable on this database") and the candidates stay `suggested`. Benchmarks for custom connections return 409.

### 5.3 Privacy, redaction, retention

* `GET /api/privacy` and a Settings section state exactly what is stored (canonical query and parameters, plan, schema metadata, findings, benchmark results, predictions, explanations, feedback; no returned rows) and the deletion controls.
* `DELETE /api/analyses/{id}` removes an analysis and everything under it.
* A logging filter redacts SQL string/number literals and parameter values in all application loggers; SQLAlchemy engines use `hide_parameters=True`.

### 5.4 Chaos and adversarial tests (`@pytest.mark.chaos`, run explicitly)

Lock contention (another session holds `ACCESS EXCLUSIVE` on a table: the analysis fails within seconds via `lock_timeout`, not minutes), target database restart during an analysis (the analysis ends `failed`; the next one succeeds), worker killed mid-benchmark (the janitor fails the stuck run and removes the clone), plus the existing adversarial set extended to the new endpoints.

## 6. Slice v0.7 — MongoDB

### 6.1 Adapter architecture

`DatabaseAdapter` (the guide's interface: `validate_query`, `collect_schema`, `explain`, `normalize_plan`, `collect_workload`, `generate_candidates`, `benchmark`) with `PostgresAdapter` wrapping the existing functions (behaviour unchanged, existing tests stay green) and `MongoAdapter`. The analysis pipeline and the benchmark job call the adapter selected by `analysis.engine`. `ParsedPlan` gains `engine`; `PlanNode` gains optional `docs_examined` and `keys_examined` (null for PostgreSQL).

### 6.2 Queries and safety

JSON query specs: `{"find": "orders", "filter": {...}, "sort": {...}, "projection": {...}, "limit": n}` or `{"aggregate": "orders", "pipeline": [...]}`. Parameters are `{"$param": n}` placeholders replaced by validated scalars. The validator allows only `find` and `aggregate`; rejects `$out`, `$merge`, `$function`, `$accumulator`, `$where`, `mapReduce`, `$currentOp`-style admin stages, unknown top-level keys, excessive nesting/size; stage allowlist for pipelines. Execution uses `maxTimeMS` (10 s), a read-only user, and the same size limits.

### 6.3 Plans and rules

`explain` with `executionStats`; `normalize_plan` maps the stage tree (`inputStage`/`inputStages`) onto `PlanNode` (`nReturned` -> actual rows, `docsExamined`/`keysExamined`, `executionTimeMillisEstimate` -> time, exclusive time by subtraction; no estimates, so `estimated_*` are zero and the UI hides "est" for MongoDB). Ten rules (`MONGO_*`): collection scan on a large collection; scan amplification `docsExamined / max(nReturned,1)`; key amplification; blocking sort; inefficient compound-index order (equality after range, sort not served); unbounded aggregation stage; large `$lookup` without a supporting index; unselective index; redundant index (from the index list, prefix coverage); pipeline stage ordering (a `$match` that could move before `$group`/`$project`/`$sort`).

### 6.4 Candidates and benchmark

Compound index candidates by the Equality-Sort-Range rule, pruned against existing indexes (prefix redundancy). Candidates reuse `IndexCandidate` (`columns` with direction); the `createIndex` command is built from structure, never from a string. There is no hypothetical index, so validation is `suggested` -> `benchmark_validated` only. **Sandbox:** a throwaway database `bench_<id>` on the same MongoDB server, filled by `$out` from the referenced collections (and `$lookup` targets); the benchmark follows the same protocol (warm-up, 5-10 measured runs of server-reported `executionTimeMillis`, create the real index, re-measure, compare medians, drop). The output checksum is computed server-side in an aggregation (`count` and the sum of `$mod(toHashedIndexKey($$ROOT), 1000003)`), which is order-insensitive, so no documents leave the database.

### 6.5 Lab and workload

A `mongo` service (`mongo:7`) with two users: a read-only user on the sample database and a bench user able to create and drop `bench_*` databases. The seeder converts the shop generator's output into collections (`customers`, `orders` with embedded items and payment, `events`) with a few deliberate existing indexes, including a redundant pair on `events`. Eight slow queries (collection scan by customer; status + date range; blocking sort; range-before-equality index; `$lookup` without index; `$match` after `$group`; unbounded `$group`; unselective status index). The profiler (level 2) feeds the workload importer: `system.profile` entries are grouped by normalized query shape (literals replaced by `?`), with the same panels as PostgreSQL.

### 6.6 UI

An engine selector on New analysis, a JSON editor, per-engine sample queries, DDL shown as `db.orders.createIndex({...})`, plan nodes showing docs examined / keys examined / returned, the engine as a badge in history and workload.

### 6.7 Tests

Validator (allowlists, placeholders, hostile pipelines), normalization on committed explain fixtures, every rule (positive/negative), ESR candidate generation and redundancy, shape normalization, live integration (explain, sandbox clone, benchmark, checksum, nothing left behind), golden tests for the eight queries, and the Playwright journey for MongoDB.

## 7. Risks and mitigations

* **ML may not beat the heuristic** (the heuristic already encodes strong signal and the dataset is synthetic): the evaluation is honest, `use_for_ranking` is data-driven and defaults to the heuristic, and the UI explains why.
* **Dataset generation time** (about an hour of benchmarks): shared baselines per query instance, run in the background while other slices are built.
* **Ollama has no chat model installed here** (only an embedding model): the adapter is exercised against a fake local HTTP server in tests; the template explainer is the always-on path; enabling the LLM is `ollama pull <model>` plus `QD_OLLAMA_MODEL`.
* **Adapter refactor risk:** the Postgres adapter wraps existing functions and the existing 400+ tests stay green throughout.
* **MongoDB image size (~800 MB)** and a second database technology in the stack: isolated behind the `mongo` service and the adapter.
* **Scope:** strict slice order with a verification gate and tag after each; the ledger records every deviation.
