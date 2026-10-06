# QueryDoctor v0.1–v0.3 Design (Phases 0–6)

Date: 2026-10-05
Status: Draft for user review

## 1. Purpose and success criteria

QueryDoctor is an evidence-based PostgreSQL performance investigator. Given a slow `SELECT`, it reads the execution plan, diagnoses bottlenecks with cited evidence, tests candidate indexes (planner-only via HypoPG, then measured in an isolated sandbox), and recommends only changes supported by measurements. An LLM is never part of this release.

This spec covers the first public release: Phases 0–6 of the build guide (sample lab, control plane, plan parser and viewer, diagnosis engine, index-candidate engine, benchmark engine), delivered as three demoable slices (v0.1, v0.2, v0.3).

Success = the Definition of Done items that apply to this release:

- Diagnoses at least eight distinct failure patterns (10 rules implemented).
- Every finding cites plan evidence.
- Nothing is ever applied automatically.
- Candidate indexes are checked for redundancy.
- At least one candidate is tested with HypoPG.
- Benchmarks run in an isolated database, with repeated measurements.
- The UI distinguishes `suggested`, `planner_validated` and `benchmark_validated`.
- Backend, rule engine and the main user journey have automated tests.
- The demo uses prepared sample queries and needs no credentials.
- README documents architecture, security decisions and benchmark methodology (screenshots added once the UI runs).

Explicit non-goals for this release: authentication/workspaces, arbitrary user database connections, DML/DDL analysis, ML ranking, LLM explanations, MongoDB, `pg_stat_statements` workload import (the extension is preloaded but unused), OpenTelemetry/Sentry.

## 2. Architecture

```
Next.js UI  ->  FastAPI  ->  metadata Postgres (querydoctor-db)
                  |
                  +-> Redis -> Celery worker -> target-postgres  (explore: EXPLAIN, HypoPG)
                                            \-> bench-postgres   (sandbox clones: real indexes, timings)
```

Two databases stay separate: the QueryDoctor metadata DB (never reachable from analysis code paths) and the target databases being investigated.

### Sandbox decision

Benchmarks use a **template-database clone inside a dedicated `bench-postgres` instance**: `CREATE DATABASE bench_<id> TEMPLATE querydoctor_sample`, run, `DROP DATABASE`. Chosen over per-run containers (needs Docker socket, slow and brittle on Windows) and `pg_dump` restores (minutes per candidate). The explore instance is never mutated. Isolation is database-level, acceptable because only QueryDoctor-controlled sample data is used in this release.

## 3. Repository layout

```
query_doctor/
├── apps/
│   ├── api/                  # FastAPI app + Celery worker (same image, two commands)
│   │   ├── app/{api,models,schemas,services,workers,security}/
│   │   ├── alembic/
│   │   └── tests/
│   └── web/                  # Next.js + TS + Tailwind
│       ├── app/  components/{query-editor,plan-viewer,findings,benchmarks}/  lib/
├── packages/
│   └── querydoctor/          # ONE installable Python distribution
│       └── src/querydoctor/
│           ├── safety/       # SQL validator, session guards
│           ├── plan_parser/  # raw EXPLAIN JSON -> PlanNode, derived metrics
│           ├── diagnosis/    # rule protocol, 10 rules, engine
│           ├── candidates/   # extraction, generation, redundancy, HypoPG scoring
│           ├── benchmark/    # sandbox, runner, stats, reliability checks, report
│           └── lab/          # data generator, slow-query catalog
├── fixtures/{postgres_plans,sample_queries}/
├── infrastructure/{docker/,compose.yaml}
└── docs/{architecture.md,security.md,rule-catalog.md,superpowers/specs/}
```

The four guide packages are subpackages of one distribution `querydoctor` (module boundaries preserved, one pyproject). `apps/api` depends on it and is the only layer touching HTTP, Celery, SQLAlchemy.

## 4. Infrastructure (Docker Compose)

Services: `web`, `api`, `worker`, `redis`, `querydoctor-db`, `target-postgres`, `bench-postgres`.

- Postgres images are PG16 + `postgresql-16-hypopg` (installed from PGDG in a thin Dockerfile). `shared_preload_libraries=pg_stat_statements` set at startup. `CREATE EXTENSION hypopg` in the target and in each sandbox template.
- A one-shot `seed` job generates the sample DB into `target-postgres`; `bench-postgres` receives the same data (generated with the same seed) as `querydoctor_sample`, the template.
- A least-privilege role `qd_readonly` (no superuser; `SELECT` on sample schema; `default_transaction_read_only=on`) is used for all explore sessions. A separate `qd_bench` role may create/drop databases and indexes **only on bench-postgres**.
- `docker compose up` yields a populated database and the slow-query catalog.

### Sample data

E-commerce schema: `customers, products, orders, order_items, payments, events`. A seeded deterministic generator (numpy RNG, fixed seed) with realistic skew: Zipf-distributed customers per order and products per item, seasonality in `created_at`, uneven order statuses, correlated columns (e.g. status vs. timestamps, country vs. currency). Data loaded with `COPY` for speed, then `ANALYZE`.

| Scale | customers | orders | order_items | events | Use |
|---|---|---|---|---|---|
| `small` (default for tests/CI) | 10k | 100k | 300k | 500k | automated tests |
| `full` | 100k | 1M | 3M | 5M | demo |

Deliberately only primary keys exist (plus those FKs' implicit lack of indexes) so the slow-query patterns are real.

### Slow-query catalog (8 queries, each with parameters, description, expected finding, expected candidate)

1. Recent orders for a customer (no index on `customer_id, created_at`).
2. Orders by `status` and date range.
3. Join orders–payments on an unindexed key.
4. Large result with unnecessary `ORDER BY`.
5. Function applied to an indexed column (`date_trunc` / `lower(email)`).
6. `SELECT *` wide result.
7. Correlated subquery.
8. Join with badly estimated cardinality (correlated filters).

Exit criterion: at least five show distinct slow-plan patterns on the `full` and `small` scales.

## 5. Safety (built before anything executes SQL)

`querydoctor.safety`:

- **Validator** (SQLGlot, dialect postgres): exactly one statement; root must be `SELECT` (or `WITH ... SELECT`, no data-modifying CTEs); rejects DDL/DML, `SELECT ... INTO`, `FOR UPDATE/SHARE`, `COPY`, `SET`, and calls to a denylist of functions (`pg_sleep`, `pg_read_file`, `pg_ls_dir`, `lo_*`, `dblink*`, `pg_terminate_backend`, `set_config`, advisory locks, ...). Comments are stripped before parsing so comment-smuggled statements are rejected; size limit on query text. Returns a normalized query, fingerprint, and referenced tables.
- **Session guard**: every target connection runs `statement_timeout=10s`, `lock_timeout=1s`, `idle_in_transaction_session_timeout=15s`, `default_transaction_read_only=on`, inside an explicit transaction that is always rolled back; connection role is non-superuser. Row cap enforced by never returning business rows (only plans and checksums).
- **EXPLAIN ANALYZE gate**: only runs when the request has `execute=true`; an `audit_events` row records the approval. Plain `EXPLAIN` otherwise.
- **Parameters**: `$n` placeholders bound as typed values. Planner-only `EXPLAIN` uses `PLAN_CACHE_MODE`-forced generic/custom plan handling via literal inlining through the driver's server-side binding (`PREPARE` + `EXPLAIN EXECUTE`), so parameters are never string-concatenated.
- **Credentials**: Fernet-encrypted at rest with an app key from env; deletion endpoint. `/connections/test` only succeeds for allowlisted sample hosts in this release.
- **Audit events**: analysis started, execute-explain requested, benchmark approved, sandbox index created, job cancelled, report exported.
- Redaction: literals are stripped from log lines; stored query and plan size limits.

## 6. Engine pipeline

Each stage is a pure function over data, unit-tested with committed fixtures; only the thin DB-access layer needs Postgres.

### 6.1 plan_parser

Input: PostgreSQL `EXPLAIN (FORMAT JSON, VERBOSE, COSTS, SETTINGS)` or with `ANALYZE, BUFFERS, WAL`. Output: normalized `PlanNode` tree (pydantic) matching the guide's model (ids, parent, node type, relation/index, estimated/actual rows, cost, actual time, loops, rows removed by filter, shared hit/read, temp read/written, filter/index/join conditions, children), plus plan-level planning/execution time and settings.

Derived metrics per node: estimation error ratio, filter waste, exclusive time (accounting for `loops` and parallel workers), I/O pressure classification (memory / disk / temp). Plan fingerprint (structure-only hash: node types, relations, index names) for comparing plans. The frontend only ever sees the normalized form.

### 6.2 diagnosis

`DiagnosisRule` protocol (`rule_id`, `supported_node_types`, `evaluate(node, context) -> Finding | None`) and `Finding` model as in the guide. `AnalysisContext` carries the whole plan, table stats (row counts from `pg_class`), existing indexes, and whether the plan was executed. Rules needing actual numbers degrade to estimates and lower confidence when only `EXPLAIN` was run.

Ten rules, each with fixture-based unit tests (positive and negative):

| Rule id | Detects |
|---|---|
| `PG_SEQ_SCAN_FILTER_WASTE` | Large seq scan discarding most rows |
| `PG_HIGH_FILTER_WASTE` | Any scan node with high rows-removed ratio |
| `PG_CARDINALITY_MISESTIMATE` | Estimation error ratio over threshold |
| `PG_REPEATED_NESTED_LOOP` | High loops × inner work |
| `PG_SORT_SPILL` | External sort / temp blocks |
| `PG_HASH_SPILL` | Hash batches > 1 / temp I/O |
| `PG_INDEX_FILTER_REMAINDER` | Index scan still filtering many rows |
| `PG_FUNCTION_ON_COLUMN` | Function/cast on filtered column blocking index |
| `PG_WIDE_RESULT` | `SELECT *` / large row width |
| `PG_DUPLICATE_SUBTREE` | Repeated equivalent subtree |

Each finding answers: what happened, where (`node_ids`), measurements (`evidence`), what might help (`next_actions`), whether tested (validation state), and the risk/tradeoff. Thresholds live in a config object, documented in `docs/rule-catalog.md`.

### 6.3 candidates

- **Extraction** (SQLGlot AST + plan): equality predicates, range predicates, join keys, `ORDER BY`, `GROUP BY`, selected columns, per table.
- **Generation** ordering: equality, join keys, range, ordering columns, then `INCLUDE` for output columns. Variants: key-only, covering (`INCLUDE`), partial (when a constant equality on low-cardinality column dominates), expression index (for `PG_FUNCTION_ON_COLUMN`).
- **Redundancy elimination**: drop candidates equal to, or a left-prefix of, an existing index or another candidate; check column selectivity, table size, estimated index width, and write overhead note; skip when a cast prevents index use (suggest expression index instead).
- **HypoPG scoring**: per candidate, in a dedicated read-only session on `target-postgres`: `hypopg_create_index`, planner-only `EXPLAIN`, record whether the planner picked the hypothetical index, compare with the original (cost, estimated rows, operators, plan fingerprint), `hypopg_reset`. Keep only candidates with meaningful improvement (configurable, default ≥10% cost reduction or operator improvement).
- **Planner score** = `0.35·cost_reduction + 0.25·rows_reduction + 0.20·plan_risk_reduction + 0.20·operator_improvement` (each normalized to 0–1), documented as evidence not proof.
- Validation states: `suggested` (rule-derived) → `planner_validated` → `benchmark_validated`.

### 6.4 benchmark

For each user-approved candidate, on `bench-postgres`:

1. Create sandbox `bench_<id>` from the template; record PG version, relevant settings, dataset snapshot id.
2. Warm-up run, then N measured runs (default 7, configurable 5–10) of the original query via `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`; capture planning time, execution time, buffers, temp blocks, rows.
3. Create the real index (`CREATE INDEX`), time it, measure its size, run `ANALYZE`.
4. Warm-up and N measured runs of the same query with identical parameters.
5. Compare result checksums of the actual query output (original vs. candidate, order-normalized unless `ORDER BY` present) to detect semantic differences.
6. Store every individual measurement; compute median, p95, variance (coefficient of variation), median improvement; teardown sandbox (always, in `finally`).

Reliability warnings: high variance (CV threshold), result dominated by first-run cache warming, too few iterations, differing parameters/dataset snapshot, differing output checksums. A cap on concurrent benchmarks and a per-benchmark timeout prevent runaway jobs. Candidates that regress performance are reported as regressions, not hidden.

Report: median/p95 before and after, improvement %, index size, index build time, plan fingerprint change, warnings, methodology note. Exported as Markdown and standalone HTML.

## 7. Control plane

FastAPI routes (from the guide):

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/connections/test` | Test an allowlisted target connection |
| POST | `/api/projects` | Create project (seed project "Sample shop" at startup) |
| GET | `/api/projects/{id}/analyses` | List previous analyses |
| POST | `/api/analyses` | Submit query (`sql`, `params`, `execute`, `project_id`) |
| GET | `/api/analyses/{id}` | Full analysis (plan, findings, candidates) |
| GET | `/api/analyses/{id}/events` | SSE progress |
| POST | `/api/analyses/{id}/cancel` | Cancel |
| POST | `/api/analyses/{id}/candidates/{cid}/benchmark` | Approve a benchmark |
| GET | `/api/analyses/{id}/report` | Export (`?format=md\|html`) |
| GET | `/api/sample-queries` | The slow-query catalog |

Progress states: `validating_query → collecting_schema → generating_plan → diagnosing → testing_candidates → completed` (benchmark jobs add `benchmarking` and `benchmark_completed`). Celery tasks publish events to a Redis stream/pubsub; the API relays them as SSE and the latest state is always readable from the DB so a late subscriber catches up. Cancellation revokes the task, cancels the backend with `pg_cancel_backend` on the tracked PID, and always drops sandboxes.

Metadata schema (SQLAlchemy 2 + Alembic): `projects, connections, query_snapshots, analyses, findings, recommendation_candidates, benchmark_runs, audit_events`. Raw plan JSON is stored on `analyses` (size-capped); the normalized tree is rebuilt on read. No business rows are stored. Users/workspaces/model tables are deferred.

## 8. Web UI

Next.js (App Router), TypeScript, Tailwind. Pages: Dashboard, Projects, New analysis, Analysis result, Settings (minimal).

- **Query editor**: Monaco SQL editor, parameter editor, sample-query picker, "Explain only" vs "Execute with analysis" with a prominent warning that execute runs the query, history panel.
- **Plan viewer**: React Flow tree built from normalized nodes; each node shows operator, table/index, actual vs. estimated rows, time, loops, buffer reads, warning badge; severity colors green/yellow/orange/red; click opens a detail panel (what the operator does, evidence, why it may be slow, related finding/candidate, raw JSON fragment).
- **Findings**: cards with severity, evidence table, next actions, linked nodes.
- **Candidates**: cards with DDL, planner score, plan-change summary, validation badge (suggested / planner_validated / benchmark_validated), "Approve benchmark" button (explicit confirmation).
- **Benchmarks**: Recharts before/after latency (individual runs, medians, p95), index size/build time, reliability warnings, export report.
- **History**: previous analyses per project with fingerprint and key metrics.
- SSE hook drives a live progress stepper. Typed API client generated from the FastAPI OpenAPI schema.

## 9. Testing

- **Unit (pytest)**: plan normalization, every rule (positive/negative), candidate construction, redundancy detection, plan comparison, fingerprinting, SQL safety validator (including adversarial inputs), benchmark statistics. Uses committed fixtures in `fixtures/postgres_plans/`; no DB required.
- **Golden**: for each catalog query, assert expected finding, expected candidate, expected plan change (seq scan → index scan) against fixtures and, in integration, against live Postgres.
- **Integration (pytest, Docker Postgres at `small` scale)**: seed, submit analysis, collect plan, generate candidate, HypoPG evaluation, sandbox benchmark, identical-results check, improvement report. Plus failure cases: timeout, lock contention, invalid connection, worker crash/cancel leaves no sandbox, huge plan, candidate that regresses, candidate that changes output.
- **Frontend**: Vitest for the plan-to-graph mapping and components; Playwright E2E for the main journey (open sample project, select query, run analysis, open node, inspect finding, approve benchmark, view comparison, export report).
- Integration tests are marked and skippable when Docker is not available.

## 10. Delivery slices

1. **v0.1 Plan Explorer**: Compose lab + seed + catalog, safety module, plan_parser, first five rules, FastAPI + Celery + SSE + metadata DB, UI with editor and plan viewer and findings.
2. **v0.2 Recommendation Engine**: remaining rules (all ten), candidate extraction/generation/redundancy, HypoPG scoring, plan comparison, candidates UI.
3. **v0.3 Benchmark Lab**: sandbox benchmark engine, reliability checks, comparison charts, report export, Playwright E2E, README with methodology.

Each slice ends demoable with its tests green before the next starts.

## 11. Risks and mitigations

- **Data volume on a laptop** (`full` scale): configurable scale, tests use `small`; generation uses `COPY` and batching.
- **HypoPG packaging**: pinned PGDG package in a thin Dockerfile; integration test asserts the extension loads.
- **Benchmark noise on a shared dev machine**: robust statistics, variance warnings, documented methodology, ≥5 runs.
- **Plan variation across PG versions**: pin PG16 for fixtures and containers; parser tolerant of missing fields.
- **Scope**: strict slice order; later phases (workload, ML, LLM, MongoDB) are out of scope for this spec.
