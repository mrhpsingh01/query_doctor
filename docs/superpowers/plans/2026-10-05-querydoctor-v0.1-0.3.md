# QueryDoctor v0.1–v0.3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the PostgreSQL-only QueryDoctor first release: sample lab, safe EXPLAIN collection, plan parser/viewer, 10-rule diagnosis engine, index candidates with HypoPG, and an isolated before/after benchmark with reports.

**Architecture:** One Python distribution `querydoctor` (pure, fixture-tested stages) under `packages/querydoctor`, consumed by a FastAPI + Celery app (`apps/api`) backed by a metadata Postgres and Redis, and a Next.js UI (`apps/web`). Targets are `target-postgres` (explore/HypoPG) and `bench-postgres` (template-clone sandboxes).

**Tech Stack:** Python 3.12, pydantic 2, SQLGlot, psycopg 3, numpy, FastAPI, SQLAlchemy 2 + Alembic, Celery + Redis, pytest; Next.js (App Router) + TypeScript + Tailwind, Monaco, @xyflow/react, Recharts, Vitest, Playwright; Docker Compose with PG16 + HypoPG.

**Spec:** `docs/superpowers/specs/2026-10-05-querydoctor-v0.1-0.3-design.md`

## Global Constraints

- PostgreSQL 16 only; `SELECT` / `WITH … SELECT` only; one statement per query. Never auto-apply a change.
- Target sessions: `statement_timeout=10s`, `lock_timeout=1s`, `idle_in_transaction_session_timeout=15s`, `default_transaction_read_only=on`, always rolled back, non-superuser role `qd_readonly`. `qd_bench` role only on bench-postgres.
- `EXPLAIN ANALYZE` only when request has `execute=true`; writes an `audit_events` row.
- No business rows are ever stored or returned (plans, stats, checksums only).
- Validation states exactly: `suggested`, `planner_validated`, `benchmark_validated`. Finding severities: `info`, `warning`, `critical`.
- Rule ids exactly: `PG_SEQ_SCAN_FILTER_WASTE`, `PG_HIGH_FILTER_WASTE`, `PG_CARDINALITY_MISESTIMATE`, `PG_REPEATED_NESTED_LOOP`, `PG_SORT_SPILL`, `PG_HASH_SPILL`, `PG_INDEX_FILTER_REMAINDER`, `PG_FUNCTION_ON_COLUMN`, `PG_WIDE_RESULT`, `PG_DUPLICATE_SUBTREE`.
- Planner score = `0.35·cost_reduction + 0.25·rows_reduction + 0.20·plan_risk_reduction + 0.20·operator_improvement`.
- Benchmarks: default 7 measured runs (allowed 5–10), 1 warm-up, medians + p95, teardown in `finally`.
- Scales: `small` 10k/100k/300k/500k, `full` 100k/1M/3M/5M (customers/orders/order_items/events); fixed RNG seed.
- Host ports: web 3000, api 8000, target-postgres 5433, bench-postgres 5434, querydoctor-db 5435, redis 6379.
- Scope exclusions: no auth/workspaces, no arbitrary connections, no ML/LLM/MongoDB/OTel.

## Review Focus

1. User pastes SQL with trailing `;`, leading comments or a typed `EXPLAIN …` prefix → trailing `;` accepted, `EXPLAIN` rejected with a clear message (Task 2).
2. Param count mismatch / non-JSON-typed values (dates as strings) → HTTP 422, never a 500 (Tasks 8, 10).
3. Plans with no actual data, `Never executed` nodes, zero-row estimates, `loops>1`, parallel workers → parser and rules never crash or divide by zero (Tasks 3, 4, 15).
4. Query referencing a missing table or timing out → analysis ends `failed` with a message and never hangs (Tasks 8, 10).
5. Benchmark cancel/crash/double-approve → sandbox always dropped, approval idempotent (Tasks 22, 23).

---

## Shared Interfaces (referenced by tasks; all under `packages/querydoctor/src/querydoctor/`)

```python
# safety/validator.py
class ValidationError(ValueError): code: str            # e.g. "multiple_statements", "not_select", "denied_function", "explain_not_allowed", "too_long", "parse_error"
class ValidatedQuery(BaseModel): sql: str; normalized_sql: str; fingerprint: str; tables: list[str]; param_count: int; has_select_star: bool
def validate_query(sql: str, *, max_chars: int = 20_000) -> ValidatedQuery

# schema.py
class IndexInfo(BaseModel): name: str; table: str; columns: list[str]; include: list[str] = []; unique: bool = False; predicate: str | None = None; size_bytes: int | None = None
class TableInfo(BaseModel): name: str; row_estimate: int; columns: dict[str, str]      # column -> type
class SchemaSnapshot(BaseModel): tables: dict[str, TableInfo]; indexes: dict[str, list[IndexInfo]]

# plan_parser/
class PlanNode(BaseModel): node_id: str; parent_id: str | None; node_type: str; relation_name: str | None; index_name: str | None
    estimated_rows: float; actual_rows: float | None; estimated_cost: float; actual_time_ms: float | None; loops: int | None
    rows_removed_by_filter: int | None; shared_hit_blocks: int | None; shared_read_blocks: int | None; temp_read_blocks: int | None; temp_written_blocks: int | None
    filter_condition: str | None; index_condition: str | None; join_condition: str | None
    plan_width: int | None; sort_method: str | None; hash_batches: int | None; output: list[str]
    error_ratio: float | None; filter_waste: float | None; exclusive_time_ms: float | None; io_pressure: Literal["none","memory","disk","temp"]
    raw: dict; children: list["PlanNode"]
class ParsedPlan(BaseModel): root: PlanNode; planning_time_ms: float | None; execution_time_ms: float | None; settings: dict[str, str]; executed: bool; fingerprint: str
def parse_explain(raw: str | dict | list) -> ParsedPlan
def iter_nodes(node: PlanNode) -> Iterator[PlanNode]       # pre-order

# diagnosis/
class Finding(BaseModel): rule_id: str; severity: Literal["info","warning","critical"]; title: str; summary: str; evidence: dict; node_ids: list[str]; confidence: float; next_actions: list[str]
class Thresholds(BaseModel): ...                          # all rule thresholds, defaults documented in docs/rule-catalog.md
class AnalysisContext(BaseModel): plan: ParsedPlan; schema_: SchemaSnapshot; query: ValidatedQuery | None; thresholds: Thresholds
class DiagnosisRule(Protocol): rule_id: str; supported_node_types: set[str]; def evaluate(self, node: PlanNode, context: AnalysisContext) -> Finding | None
ALL_RULES: list[DiagnosisRule]
def run_diagnosis(context: AnalysisContext) -> list[Finding]   # sorted: severity desc, then rule_id, then node_ids

# db/ (psycopg 3, sync)
def guarded_connection(dsn: str, *, role_kind: Literal["readonly","bench_admin"] = "readonly") -> ContextManager[psycopg.Connection]   # applies session guards
def explain(conn, sql: str, params: Sequence[Any], *, execute: bool) -> list[dict]   # raw JSON plan; ANALYZE variant rolls back
def collect_schema(conn, tables: Sequence[str]) -> SchemaSnapshot

# candidates/
class IndexColumn(BaseModel): name: str; desc: bool = False
class IndexCandidate(BaseModel): id: str; table: str; columns: list[IndexColumn]; include: list[str]; predicate: str | None; expression: str | None; ddl: str; rationale: list[str]; source_rule_ids: list[str]; validation: Literal["suggested","planner_validated","benchmark_validated"]
class TableUsage(BaseModel): equality: list[str]; range: list[str]; join: list[str]; order_by: list[tuple[str,bool]]; group_by: list[str]; output: list[str]
def extract_usage(sql: str) -> dict[str, TableUsage]
def generate_candidates(sql: str, schema: SchemaSnapshot, findings: list[Finding]) -> list[IndexCandidate]
def prune_redundant(cands: list[IndexCandidate], schema: SchemaSnapshot) -> tuple[list[IndexCandidate], list[tuple[IndexCandidate, str]]]
class PlanComparison(BaseModel): cost_reduction: float; rows_reduction: float; plan_risk_reduction: float; operator_improvement: float; planner_score: float; uses_candidate: bool; removed_operators: list[str]; added_operators: list[str]
def compare_plans(before: ParsedPlan, after: ParsedPlan, candidate_ddl: str) -> PlanComparison
def score_candidates(conn, sql, params, original: ParsedPlan, cands) -> list[tuple[IndexCandidate, PlanComparison, ParsedPlan]]   # HypoPG; resets in finally

# benchmark/
class RunStats(BaseModel): n: int; median_ms: float; p95_ms: float; mean_ms: float; stdev_ms: float; cv: float; min_ms: float; max_ms: float
class BenchmarkConfig(BaseModel): runs: int = 7; warmups: int = 1; cv_warn: float = 0.25; timeout_s: int = 120
class BenchmarkResult(BaseModel): before: RunStats; after: RunStats; before_samples: list[dict]; after_samples: list[dict]; improvement_pct: float; index_size_bytes: int; index_build_ms: float; plan_fingerprint_before: str; plan_fingerprint_after: str; checksum_match: bool; warnings: list[str]; pg_version: str; settings: dict; dataset_snapshot: str
def summarize(samples_ms: Sequence[float]) -> RunStats
def reliability_warnings(before: RunStats, after: RunStats, *, config: BenchmarkConfig, checksum_match: bool, first_run_ratio: float) -> list[str]
def run_benchmark(bench_dsn: str, sql: str, params: Sequence[Any], candidate_ddl: str, config: BenchmarkConfig, on_progress: Callable[[str], None] = ...) -> BenchmarkResult
def render_report(analysis: dict) -> str; def render_report_html(analysis: dict) -> str
```

Tests live in `packages/querydoctor/tests/` mirroring module paths; plan JSON fixtures in `fixtures/postgres_plans/*.json` (hand-written compact PG16-format plans, later supplemented with captures from the live lab by `scripts/capture_plans.py`). Integration tests carry `@pytest.mark.integration` and skip when Docker Postgres on 5433/5434 is unreachable.

---

# Slice v0.1 — Plan Explorer

### Task 1: Monorepo scaffold

**Files:** Create `.gitignore`, `README.md` (stub), `packages/querydoctor/pyproject.toml`, `packages/querydoctor/src/querydoctor/__init__.py`, `packages/querydoctor/tests/test_smoke.py`, `pytest.ini` (root; markers `integration`; `testpaths`).
**Produces:** installable editable package `querydoctor`; `.venv` at repo root.

- [ ] **Step 1:** Write `test_smoke.py::test_package_importable` asserting `import querydoctor; querydoctor.__version__ == "0.1.0"`.
- [ ] **Step 2:** Create `.venv`, run `pytest` → FAIL (no package).
- [ ] **Step 3:** Write the pyproject (hatchling, src layout; deps `pydantic>=2`, `sqlglot`, `psycopg[binary]>=3.2`, `numpy`; extra `dev`: `pytest`) and `__init__.py`; `pip install -e "packages/querydoctor[dev]"`.
- [ ] **Step 4:** `pytest` → PASS.
- [ ] **Step 5:** Commit `chore: scaffold monorepo and python package`.

### Task 2: SQL safety validator

**Files:** Create `safety/__init__.py`, `safety/validator.py`; Test `tests/safety/test_validator.py`.
**Produces:** `validate_query`, `ValidatedQuery`, `ValidationError`.

- [ ] **Step 1: Failing tests** (parametrized), asserting `ValidationError.code`:
  - accepted: `SELECT 1`, `select * from orders where id = $1;` (trailing `;`, `param_count==1`, `has_select_star`), `WITH a AS (SELECT 1) SELECT * FROM a`, query preceded by `-- note` and `/* c */` comments.
  - rejected: `SELECT 1; DROP TABLE orders` (`multiple_statements`), `SELECT 1 /* ; */; DELETE FROM x` (`multiple_statements`), `INSERT …`/`UPDATE …`/`DELETE …`/`CREATE INDEX …`/`DROP …`/`COPY …`/`SET …` (`not_select`), `SELECT … INTO t FROM x` and `SELECT … FOR UPDATE` (`not_select`), `WITH d AS (DELETE FROM x RETURNING *) SELECT * FROM d` (`not_select`), `SELECT pg_sleep(5)`, `pg_read_file`, `set_config`, `pg_advisory_lock`, `lo_import`, `dblink(...)`, `pg_terminate_backend` (`denied_function`), `EXPLAIN ANALYZE SELECT 1` (`explain_not_allowed`), 20_001 chars (`too_long`), `SELEC 1` (`parse_error`), empty string.
  - fingerprint: `… WHERE customer_id = 123` and `= 456` give equal fingerprints and equal `normalized_sql` containing `?`; `tables` lists `orders`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement with SQLGlot (`parse(..., read="postgres")`); walk the AST for non-SELECT roots, `Into`, `Lock`, data-modifying CTEs and denylisted function names (case-insensitive, prefix match for `lo_`, `dblink`, `pg_advisory`, `pg_read`, `pg_ls`); fingerprint = sha1 of the normalized SQL with literals replaced by `?`. Verify how SQLGlot parses `$1` (Parameter vs Placeholder) and count distinct `$n`.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): add SQL validator and fingerprinting`.

### Task 3: Plan parser, derived metrics, fixtures

**Files:** Create `plan_parser/{__init__,models,parse,metrics}.py`, `schema.py`, `fixtures/postgres_plans/{seqscan_filter_waste,nested_loop_repeated,sort_spill,explain_only}.json`; Test `tests/plan_parser/test_parse.py`.
**Produces:** `PlanNode`, `ParsedPlan`, `parse_explain`, `iter_nodes`, `IndexInfo`, `TableInfo`, `SchemaSnapshot`.

- [ ] **Step 1: Failing tests:**
  - `test_normalizes_tree_ids`: `seqscan_filter_waste.json` (ANALYZE; Seq Scan on `orders` actual rows 10842, removed 1_813_561, loops 1, 742 ms) → root `node_id=="0"`, children `"0.0"`; `parent_id` correct; `relation_name=="orders"`, `rows_removed_by_filter==1813561`; `executed is True`.
  - `test_filter_waste`: `filter_waste ≈ 0.9941` (removed/(returned+removed)).
  - `test_error_ratio`: est 100 vs actual 10_000 → `100.0`; symmetrical for under-estimate; both-zero → `1.0`; est 0 / actual 5 treated as `max(est,1)` (no ZeroDivisionError).
  - `test_exclusive_time_multiplies_loops`: node `actual_time=0.5ms, loops=1000` with child 100 ms total → exclusive = `500 - child_total` clamped at `0.0`.
  - `test_explain_only_plan`: no `Actual*` keys → `executed False`, actual fields `None`, `filter_waste None`, no exception; `Never executed` node (`Actual Loops: 0`) handled.
  - `test_io_pressure`: temp written >0 → `"temp"`; shared read >0 → `"disk"`; hits only → `"memory"`.
  - `test_accepts_wrapper_forms`: str JSON, dict, and `[ {"Plan":…} ]` list all parse.
  - `test_fingerprint_ignores_numbers`: two plans differing only in costs/rows share `fingerprint`; swapping Seq Scan→Index Scan changes it.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement `parse_explain`; node ids are tree paths (`"0.1.0"`); metrics in `metrics.py` as pure functions. Exclusive time uses `Actual Total Time × Loops` minus children's same product. Fixtures are hand-written to PG16 JSON field names.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(plan_parser): normalize PostgreSQL JSON plans`.

### Task 4: Diagnosis framework + first five rules

**Files:** Create `diagnosis/{__init__,models,engine,thresholds}.py`, `diagnosis/rules/{seq_scan_filter_waste,high_filter_waste,cardinality_misestimate,repeated_nested_loop,sort_spill}.py`, `docs/rule-catalog.md`; Test `tests/diagnosis/test_rules_v1.py`.
**Consumes:** Task 3 types. **Produces:** `Finding`, `Thresholds`, `AnalysisContext`, `DiagnosisRule`, `ALL_RULES`, `run_diagnosis`.

- [ ] **Step 1: Failing tests** (positive + negative per rule, plans built from fixtures or a small `make_plan()` helper in `tests/conftest.py`):
  - `PG_SEQ_SCAN_FILTER_WASTE`: visited 1.82M, waste 0.994 → `critical`, `evidence` has `rows_visited,rows_removed,filter_waste`; waste 0.85 with 200k rows → `warning`; 50k rows → `None`; waste 0.5 → `None`; EXPLAIN-only plan with `table_rows` from schema → lower `confidence` (<0.7) and no crash.
  - `PG_HIGH_FILTER_WASTE`: Index Scan removing 90% → finding; Seq Scan nodes are skipped (owned by the rule above).
  - `PG_CARDINALITY_MISESTIMATE`: ratio ≥ 10 → `warning`, ≥ 100 → `critical`; not emitted without actuals.
  - `PG_REPEATED_NESTED_LOOP`: inner node `loops ≥ 1000` with exclusive time ≥ 20% of total → finding.
  - `PG_SORT_SPILL`: `sort_method == "external merge"` or temp written > 0 on a Sort → finding naming the node.
  - `run_diagnosis` returns severity-descending, deterministic order; thresholds overridable via `Thresholds(...)`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement rules as small classes conforming to `DiagnosisRule`; the engine walks `iter_nodes` and calls rules whose `supported_node_types` contain the node type (empty set = all). Thresholds default: `seq_scan_min_rows=100_000, waste_warn=0.80, waste_critical=0.95, misestimate_warn=10, misestimate_critical=100, loops_min=1000`. Document each in `docs/rule-catalog.md`.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(diagnosis): rule framework and first five rules`.

### Task 5: Lab database images and Compose DB services

**Files:** Create `infrastructure/docker/postgres-hypopg.Dockerfile`, `infrastructure/docker/initdb/{01_roles.sql,02_extensions.sql}`, `infrastructure/compose.yaml` (services `target-postgres`, `bench-postgres`, `querydoctor-db`, `redis` for now), `.env.example`.

- [ ] **Step 1:** Dockerfile `FROM postgres:16`, `apt-get install postgresql-16-hypopg`, command sets `shared_preload_libraries=pg_stat_statements`. `01_roles.sql` creates `qd_readonly` (LOGIN, no superuser, default read-only, `ALTER ROLE … SET statement_timeout`) and, for bench, `qd_bench` with `CREATEDB`; `02_extensions.sql` creates `pg_stat_statements` and `hypopg`. Ports per Global Constraints.
- [ ] **Step 2:** Verify (Docker Desktop must be unpaused): `docker compose -f infrastructure/compose.yaml up -d target-postgres bench-postgres querydoctor-db redis`; `psql … -c "select hypopg_reset(); select 1 from pg_extension where extname='pg_stat_statements'"` succeed against 5433 and 5434. Expected: both extensions present, `qd_readonly` is not superuser.
- [ ] **Step 3:** Commit `feat(infra): postgres+hypopg images and compose db services`.

### Task 6: Seeded data generator and loader

**Files:** Create `lab/{__init__,generator,schema_sql,seed}.py`; Test `tests/lab/test_generator.py` (unit) and `tests/lab/test_seed_integration.py` (integration).
**Produces:** `lab.generator.generate(scale: Literal["small","full"], seed: int = 42) -> Iterator[tuple[str, Iterable[tuple]]]` (table name, row batches); `python -m querydoctor.lab.seed --dsn … --scale small` creating schema + `COPY` loading + `ANALYZE`.

- [ ] **Step 1: Failing unit tests:** row counts match Global Constraints for `small`; same seed → identical first 100 rows of `orders`, different seed → different; top 1% of customers own ≥ 20% of orders (Zipf skew); order statuses uneven (most common > 45%, least < 5%); `created_at` shows seasonality (a December month has > 1.3× the mean monthly count); `payments.order_id` and `orders.customer_id` reference existing ids.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement with numpy (`default_rng(seed)`, Zipf-like weighted sampling); `schema_sql.py` holds DDL with PK indexes **only** (FK columns unindexed on purpose). Tables: `customers(id, email, country, created_at, segment)`, `products(id, name, category, price)`, `orders(id, customer_id, status, total_amount, currency, created_at, shipped_at)`, `order_items(id, order_id, product_id, quantity, unit_price)`, `payments(id, order_id, amount, method, paid_at)`, `events(id, customer_id, event_type, payload, created_at)`. Load via psycopg `COPY … FROM STDIN`, then `ANALYZE`.
- [ ] **Step 4:** Unit tests PASS; integration test seeds `small` into target-postgres and asserts `count(*) from orders = 100000` and no non-PK index exists.
- [ ] **Step 5:** Seed both instances: target-postgres (explore) and bench-postgres as database `querydoctor_sample` (the template). Commit `feat(lab): deterministic skewed sample data generator`.

### Task 7: Slow-query catalog

**Files:** Create `lab/catalog.py`, `fixtures/sample_queries/catalog.json` (generated from `catalog.py`); Test `tests/lab/test_catalog.py`.
**Produces:** `CATALOG: list[SampleQuery]` with fields `id, title, sql, params, description, expected_rule_ids, expected_candidate_columns: list[str] | None`.

- [ ] **Step 1: Failing tests:** 8 queries with unique ids matching the spec list; each passes `validate_query`; `param_count == len(params)`. Integration: for ≥ 5 queries `explain(…, execute=False)` plan root-subtree contains a `Seq Scan` on the expected table (distinct patterns recorded in `expected_rule_ids`).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Write the 8 queries from spec §4 (customer recent orders; status+date range; orders–payments join; unnecessary ORDER BY on large result; `date_trunc`/`lower(email)` on column; `SELECT *` wide; correlated subquery; correlated filters e.g. `country='US' AND currency='USD'`).
- [ ] **Step 4:** Run → PASS. Review: execution times on `small` visibly differ.
- [ ] **Step 5:** Commit `feat(lab): slow query catalog`.

### Task 8: Guarded connections and explain runner

**Files:** Create `db/{__init__,connection,explain,schema_collect}.py`; Test `tests/db/test_explain_integration.py`, `tests/db/test_guards.py`.
**Produces:** `guarded_connection`, `explain`, `collect_schema` (Shared Interfaces).

- [ ] **Step 1: Failing integration tests:** session has `default_transaction_read_only=on` and a write attempt (`CREATE TABLE`) raises; `SHOW statement_timeout == '10s'`; `explain(execute=False)` for catalog query 1 with params returns a plan whose `Plan` has `Node Type`; `execute=True` returns `Actual Rows`; ANALYZE of a catalog query leaves no changes and the connection is rolled back; a 10s-bounded `SELECT` with a tiny timeout override raises `QueryCanceled` mapped to `ExplainTimeout`; unknown table raises `ExplainFailed` with the Postgres message; `collect_schema(["orders"])` returns `row_estimate>0`, column types, and zero indexes except the PK; wrong param count raises `ParamMismatch` (maps to 422 later).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Bind values with psycopg server-side binding; set `plan_cache_mode = force_custom_plan`; `explain` builds `EXPLAIN (FORMAT JSON, VERBOSE, COSTS, SETTINGS)` or `(ANALYZE, BUFFERS, WAL, FORMAT JSON, VERBOSE)` and always `rollback()`. If psycopg cannot bind parameters inside `EXPLAIN`, use `PREPARE` + `EXPLAIN … EXECUTE` with typed literal rendering through `psycopg.sql.Literal` (never f-strings). Exceptions defined in `db/errors.py`.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(db): guarded connections, explain runner, schema collection`. Also add `scripts/capture_plans.py` to dump live plans for all catalog queries into `fixtures/postgres_plans/live_*.json` and commit the captures.

### Task 9: API scaffold, config and metadata models

**Files:** Create `apps/api/pyproject.toml`, `apps/api/app/{main,config,database,deps}.py`, `app/models/{__init__,project,connection,query_snapshot,analysis,finding,candidate,benchmark,audit}.py`, `apps/api/alembic.ini`, `apps/api/alembic/{env.py,versions/0001_initial.py}`, `app/security/crypto.py`; Test `apps/api/tests/{conftest,test_health,test_crypto,test_models}.py`.
**Produces:** SQLAlchemy 2 models for `projects, connections, query_snapshots, analyses, findings, recommendation_candidates, benchmark_runs, audit_events`; `get_session` dependency; `Fernet` helpers `encrypt(str)->str` / `decrypt(str)->str`; seeded project "Sample shop" on startup.

- [ ] **Step 1: Failing tests:** `GET /api/health` → `{"status":"ok"}`; crypto round-trips and ciphertext ≠ plaintext and tampered ciphertext raises; creating an `Analysis` with a `Finding` and a `RecommendationCandidate` round-trips (SQLite in-memory for unit tests via dependency override).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Columns per spec §7; JSON columns for `plan_json`, `evidence`, `samples`; enum-like strings for `status` (`queued,running,completed,failed,cancelled`). Alembic migration generated from the models; app uses `settings` read from env (`QD_DATABASE_URL`, `QD_TARGET_DSN`, `QD_BENCH_DSN`, `QD_REDIS_URL`, `QD_SECRET_KEY`).
- [ ] **Step 4:** Run → PASS; `alembic upgrade head` succeeds against `querydoctor-db` (5435).
- [ ] **Step 5:** Commit `feat(api): scaffold FastAPI app and metadata models`.

### Task 10: Analysis pipeline, Celery worker, events, SSE

**Files:** Create `app/services/{pipeline,events}.py`, `app/workers/{celery_app,tasks}.py`, `app/api/{analyses,projects,connections,samples}.py`, `app/schemas/*.py`; Test `apps/api/tests/test_analyses_api.py`, `test_pipeline.py`, `test_events.py`.
**Consumes:** Tasks 2–4, 7–9. **Produces:** routes in spec §7 (except benchmark/report); `EventBus` protocol with `publish(analysis_id, event: dict) -> None` and `read(analysis_id, after: str | None, block_ms: int) -> list[tuple[str, dict]]` (`RedisEventBus`, `InMemoryEventBus`); Celery task `run_analysis(analysis_id: int)`.

- [ ] **Step 1: Failing tests** (Celery eager mode, `InMemoryEventBus`, stub `explain`/`collect_schema` returning fixture plans):
  - `POST /api/analyses {sql, params, execute:false, project_id}` → 202 with id; after eager run `GET` returns `status=="completed"`, `plan`, `findings` (seqscan fixture → `PG_SEQ_SCAN_FILTER_WASTE`); events in order `validating_query, collecting_schema, generating_plan, diagnosing, testing_candidates, completed`.
  - invalid SQL (`DROP TABLE x`) → 422 with `code`; param count mismatch → 422; non-JSON-serializable param types → 422.
  - `execute:true` writes an `audit_events` row `execute_explain_requested`; `execute:false` does not.
  - target raising `ExplainFailed`/`ExplainTimeout` → analysis `failed` with `error` message, event `failed`.
  - `POST …/cancel` on a running analysis → `cancelled`, event emitted; cancelling a finished one → 409.
  - SSE `GET …/events` streams the stored events and closes after a terminal event; a late subscriber still receives history.
  - `GET /api/sample-queries` returns the 8 catalog items; `GET /api/projects/{id}/analyses` lists newest first; `POST /api/connections/test` rejects any host not in the allowlist (`target-postgres`, `bench-postgres`, `localhost`) with 400.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** `pipeline.run_analysis_pipeline(session, analysis, bus, deps)` is plain functions with injected `explain`/`collect_schema` so it is testable without Celery or Postgres; Celery task is a thin wrapper. Cancellation stores `cancel_requested` and the pipeline checks it between stages and calls `pg_cancel_backend` on the tracked PID (record PID via `pg_backend_pid()`). `candidates` stage is a no-op placeholder emitting `testing_candidates` until Task 19.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(api): analysis pipeline, worker, SSE progress`.

### Task 11: Full-stack Compose and API integration test

**Files:** Modify `infrastructure/compose.yaml` (add `api`, `worker`, `seed`, `web` placeholder commented); Create `infrastructure/docker/api.Dockerfile`; Test `apps/api/tests/integration/test_stack.py` (`@pytest.mark.integration`).

- [ ] **Step 1: Failing integration test:** with the stack up, `POST /api/analyses` for catalog query 1 (`execute:false`) → poll until `completed`; plan contains a `Seq Scan` on `orders`; a second request with `execute:true` returns `Actual Rows`; `DROP TABLE orders` rejected; a query on missing table ends `failed`.
- [ ] **Step 2:** Run → FAIL (no api service).
- [ ] **Step 3:** Write `api.Dockerfile` (installs `packages/querydoctor` then `apps/api`; runs `alembic upgrade head` then `uvicorn`; worker runs `celery -A app.workers.celery_app worker`); `seed` runs the seed CLI once. `docker compose up` brings everything up.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(infra): full backend compose stack`.

### Task 12: Web scaffold, API client, query editor, progress

**Files:** Create `apps/web/` via `create-next-app` (TS, Tailwind, App Router), `lib/api.ts`, `lib/useAnalysisEvents.ts`, `components/query-editor/{QueryEditor,ParamEditor,SamplePicker,ExecuteWarning}.tsx`, `app/{page,analyses/new/page,analyses/[id]/page,projects/page,settings/page}.tsx`, `next.config.ts` rewrite `/api/*` → API; Test `apps/web/__tests__/{useAnalysisEvents,QueryEditor}.test.tsx` (Vitest + Testing Library).
**Produces:** `createAnalysis`, `getAnalysis`, `listSamples`, TS types mirrored from the API schemas.

- [ ] **Step 1: Failing tests:** `useAnalysisEvents` emits steps in order from a mocked `EventSource` and stops after `completed`; `QueryEditor` shows the execute warning only when "Execute with analysis" is selected and calls `onSubmit({sql, params, execute})`; choosing a sample fills the editor and params.
- [ ] **Step 2:** Run `npm test` → FAIL.
- [ ] **Step 3:** Implement with `@monaco-editor/react` (dynamic import, no SSR); progress stepper component; dashboard lists latest analyses and links to New analysis.
- [ ] **Step 4:** `npm test` PASS; `npm run build` succeeds.
- [ ] **Step 5:** Commit `feat(web): scaffold UI, query editor and live progress`.

### Task 13: Plan viewer, node detail panel, findings

**Files:** Create `components/plan-viewer/{PlanGraph,PlanNodeCard,NodeDetailPanel,severity,layout}.ts(x)`, `components/findings/{FindingCard,FindingsList}.tsx`; Test `__tests__/{layout,severity,NodeDetailPanel}.test.ts(x)`.
**Consumes:** analysis JSON `plan.root` (normalized `PlanNode` shape) and `findings`.

- [ ] **Step 1: Failing tests:** `layoutTree(root)` returns one flow node per plan node with edges parent→child and unique positions; `severityFor(node, findings)` returns `red` for a node with a `critical` finding, `orange`/`yellow` for warning/info, `green` otherwise; the slowest node by `exclusive_time_ms` is flagged; `NodeDetailPanel` renders evidence for the finding attached to the node, the raw JSON fragment, and an operator explanation.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement with `@xyflow/react` and dagre-style layout (`@dagrejs/dagre`); node card shows operator, table/index, actual vs. estimated rows, time, loops, buffer reads, warning badge; clicking selects node and opens the panel; analysis page composes progress, plan, findings.
- [ ] **Step 4:** Run → PASS; manually load an analysis in the browser and confirm the graph renders.
- [ ] **Step 5:** Commit `feat(web): interactive plan viewer and findings`.

### Task 14: v0.1 verification

- [ ] **Step 1:** Run all backend tests (unit + integration), `npm test`, `npm run build`; bring up compose; run catalog query 1 end-to-end in the browser (browser-automation skill), confirm findings and plan node click.
- [ ] **Step 2:** Use superpowers:verification-before-completion; fix anything failing. Tag `v0.1.0`.

---

# Slice v0.2 — Recommendation Engine

### Task 15: Remaining five rules

**Files:** Create `diagnosis/rules/{hash_spill,index_filter_remainder,function_on_column,wide_result,duplicate_subtree}.py`; Modify `diagnosis/__init__.py` (`ALL_RULES`), `docs/rule-catalog.md`; Test `tests/diagnosis/test_rules_v2.py`; add fixtures `hash_spill.json, function_on_column.json, duplicate_subtree.json`.

- [ ] **Step 1: Failing tests (positive + negative each):**
  - `PG_HASH_SPILL`: `hash_batches > 1` or temp blocks on Hash/Hash Join → finding.
  - `PG_INDEX_FILTER_REMAINDER`: Index Scan with filter removing > 50% of fetched rows and a `filter_condition` on a column not in the index condition → finding with `next_actions` mentioning composite/partial index.
  - `PG_FUNCTION_ON_COLUMN`: Seq Scan whose `filter_condition` contains `lower(email)`/`date_trunc(…, created_at)`/`::text` cast on a column that has an index in `schema_` → finding; no finding when no index exists on that column; works from `context.query` AST when filter text is truncated.
  - `PG_WIDE_RESULT`: `context.query.has_select_star` and root `plan_width ≥ 200` → `info`/`warning`; narrow rows → `None`.
  - `PG_DUPLICATE_SUBTREE`: two subtrees with equal structural fingerprint and same relation/conditions → finding listing both root `node_ids`; single occurrence → `None`.
  - Review-focus: every rule returns `None` (no crash) on `explain_only.json` and zero-row estimates.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS (all ten rule ids present in `ALL_RULES`, asserted by test). **Step 5:** Commit `feat(diagnosis): complete ten-rule catalog`.

### Task 16: Predicate/usage extraction

**Files:** Create `candidates/{__init__,extraction}.py`; Test `tests/candidates/test_extraction.py`.
**Produces:** `TableUsage`, `extract_usage`.

- [ ] **Step 1: Failing tests:** for `SELECT id,total_amount FROM orders WHERE customer_id=$1 AND created_at>=$2 ORDER BY created_at DESC` → `orders: equality=["customer_id"], range=["created_at"], order_by=[("created_at",True)], output=["id","total_amount"]`; join `o JOIN payments p ON p.order_id=o.id` → `payments.join=["order_id"]` and `orders.join=["id"]`; aliases resolved to table names; `BETWEEN`/`>`/`<` are range; `IN` and `=` equality; `GROUP BY` captured; `lower(email)=$1` produces equality on the *expression* recorded separately (`expressions` list) not the bare column; subquery tables extracted; unqualified columns resolved using single-table queries.
- [ ] **Step 2–5:** Run → FAIL; implement with SQLGlot `qualify`-free scope walking; PASS; commit `feat(candidates): predicate extraction`.

### Task 17: Candidate generation and redundancy pruning

**Files:** Create `candidates/{models,generate,redundancy}.py`; Test `tests/candidates/test_generate.py`, `test_redundancy.py`.

- [ ] **Step 1: Failing tests:** for the customer-recent-orders query with an `orders` schema → candidate columns `[customer_id, created_at DESC]`, `include=["id","total_amount"]` (variant: key-only variant also generated), `ddl == 'CREATE INDEX ON orders (customer_id, created_at DESC) INCLUDE (id, total_amount)'`; column order equality → join → range → order; `PG_FUNCTION_ON_COLUMN` finding yields an expression candidate `ON users (lower(email))`; low-cardinality constant equality yields a partial-index variant; ids stable (`sha1` of ddl). Redundancy: candidate equal to existing index dropped (`"duplicate of idx_x"`); left-prefix of an existing index dropped; prefix of another candidate dropped; unique/PK-leading existing index counts; wide candidates (> 6 columns) dropped with reason; tiny tables (< 1000 rows) dropped (`"table too small"`).
- [ ] **Step 2–5:** Run → FAIL; implement; PASS; commit `feat(candidates): generation and redundancy pruning`.

### Task 18: HypoPG scoring and plan comparison

**Files:** Create `candidates/{compare,hypopg}.py`; Test `tests/candidates/test_compare.py` (pure, fixtures), `tests/candidates/test_hypopg_integration.py`.

- [ ] **Step 1: Failing tests:** `compare_plans(seq_scan_plan, index_scan_plan, ddl)` → `uses_candidate True`, `removed_operators==["Seq Scan"]`, `added_operators==["Index Scan"]`, `cost_reduction` in (0,1], `planner_score` equals the Global Constraints formula within 1e-9 and is clamped to [0,1]; a candidate the planner ignores → `uses_candidate False`, score ≈ 0; cost increase → negative improvements clamp to 0. Integration (target-postgres): `score_candidates` for catalog query 1 marks `(customer_id, created_at)` as using the hypothetical index with a lower cost; afterwards `SELECT count(*) FROM hypopg_list_indexes` is 0 even when planning raises; `pg_indexes` unchanged.
- [ ] **Step 2–5:** Run → FAIL; implement (`hypopg_create_index`, plain `EXPLAIN` in same connection, `hypopg_reset()` in `finally`; `uses_candidate` detected by plan `Index Name` containing the hypothetical index name or `<oid>btree_` pattern); PASS; commit `feat(candidates): HypoPG scoring and plan comparison`.

### Task 19: Wire candidates into pipeline, API and UI

**Files:** Modify `app/services/pipeline.py`, `app/schemas/*`, `app/api/analyses.py`; Create `components/candidates/{CandidateCard,ValidationBadge}.tsx`; Test `apps/api/tests/test_pipeline_candidates.py`, `apps/web/__tests__/CandidateCard.test.tsx`.

- [ ] **Step 1: Failing tests:** pipeline with stubbed HypoPG returns `recommendation_candidates` rows with `validation` ∈ `{suggested, planner_validated}` (candidates that change the plan are `planner_validated`, others stay `suggested` or are dropped when improvement < 10%), persisted `planner_score`, `comparison` JSON, and `dropped` list with reasons; `GET /api/analyses/{id}` includes them; UI `CandidateCard` renders DDL, score, plan-change summary and a distinct badge per validation state, plus a disabled-until-confirmed "Approve benchmark" button (Task 25 wires it).
- [ ] **Step 2–5:** Run → FAIL; implement; PASS; commit `feat: candidate recommendations in pipeline and UI`.

### Task 20: Golden tests and v0.2 verification

**Files:** Create `apps/api/tests/integration/test_golden.py`.

- [ ] **Step 1:** For each catalog query, assert the expected rule ids appear and the expected candidate columns (where defined) are present and `planner_validated` (live stack). Expected: Query 1 → `PG_SEQ_SCAN_FILTER_WASTE` + `(customer_id, created_at)` index changing Seq Scan → Index Scan.
- [ ] **Step 2:** Fix generator/rules mismatches found (adjust thresholds or catalog `expected_*` honestly, not by weakening the assertion without cause); rerun all suites; tag `v0.2.0`; verification-before-completion.

---

# Slice v0.3 — Benchmark Lab

### Task 21: Benchmark statistics and reliability checks

**Files:** Create `benchmark/{__init__,stats,reliability}.py`; Test `tests/benchmark/test_stats.py`.

- [ ] **Step 1: Failing tests:** `summarize([10,12,11,100,10,11,12])` → `median_ms==11`, `p95_ms` by linear interpolation, `cv>0.25`; `summarize` on `<1` sample raises; `reliability_warnings` returns strings containing `"high variance"` (cv > `cv_warn`), `"too few runs"` (n < 5), `"output differs"` (`checksum_match False`), `"cache warming"` (first_run_ratio > 3), none for a clean case; improvement pct formula `(before−after)/before×100` tested incl. regression → negative.
- [ ] **Step 2–5:** Run → FAIL; implement; PASS; commit `feat(benchmark): statistics and reliability warnings`.

### Task 22: Sandbox and benchmark runner

**Files:** Create `benchmark/{sandbox,checksum,runner,models}.py`; Test `tests/benchmark/test_sandbox_integration.py`, `test_runner_integration.py`.

- [ ] **Step 1: Failing integration tests (bench-postgres, `small`):** `Sandbox.create()` makes `bench_<id>` from `querydoctor_sample`, `drop()` removes it (even when a session is still connected — terminates backends first); the context manager drops on exception; `result_checksum` equal for identical queries and different when output differs; ordered and unordered queries handled (set-compare when no `ORDER BY`); `run_benchmark` for catalog query 1 + `CREATE INDEX ON orders (customer_id, created_at DESC) INCLUDE (id, total_amount)` returns 7 samples each side, `after.median_ms < before.median_ms`, `index_size_bytes>0`, `index_build_ms>0`, `checksum_match True`, `plan_fingerprint_after != plan_fingerprint_before`; raising mid-run leaves no `bench_%` database; an invalid candidate DDL fails cleanly with `BenchmarkFailed` and still tears down; `Sandbox.drop_stale(max_age_s)` drops leftover `bench_%` databases older than the cutoff; progress callback receives `creating_sandbox, warmup_before, measuring_before, creating_index, analyzing, measuring_after, comparing, teardown`; timeout enforced.
- [ ] **Step 2–5:** Run → FAIL; implement per spec §6.4 (`CREATE DATABASE … TEMPLATE`, `qd_bench` role, `statement_timeout` per run, `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` for timings, server-side checksum `md5(string_agg(t::text, ',' [ORDER BY t::text]))`, concurrency cap via a Redis/`threading.BoundedSemaphore`-style lock at the worker level); PASS; commit `feat(benchmark): sandbox and benchmark runner`.

### Task 23: Benchmark worker task, approval API, report

**Files:** Create `benchmark/report.py`, `app/api/benchmarks.py`, `app/workers/benchmark_tasks.py`; Modify `app/models/benchmark.py`; Test `tests/benchmark/test_report.py`, `apps/api/tests/test_benchmarks_api.py`.

- [ ] **Step 1: Failing tests:** `POST /api/analyses/{id}/candidates/{cid}/benchmark` → 202, creates `benchmark_runs` row, writes `benchmark_approved` audit event; second identical call while queued/running returns the same run (idempotent, 200); completed run flips candidate `validation` to `benchmark_validated` only when `improvement_pct>0` and no blocking warning (`output differs` blocks it); regression stays `planner_validated` with result stored and warning; benchmark of a non-existent candidate → 404; events `benchmarking`, `benchmark_completed` (or `failed`); cancel drops the sandbox (stub runner asserts teardown called); `GET …/report?format=md|html` renders method, medians, p95, improvement, index size/build time, warnings, plan change, `audit_events` row `report_exported`; Markdown contains no business rows and no connection strings.
- [ ] **Step 2–5:** Run → FAIL; implement; PASS; commit `feat: benchmark approval API, worker and reports`.

### Task 24: Benchmark UI and history

**Files:** Create `components/benchmarks/{BenchmarkPanel,LatencyChart,RunTable,Warnings}.tsx`, `app/projects/[id]/page.tsx` history list; Modify `CandidateCard.tsx`; Test `__tests__/{BenchmarkPanel,LatencyChart}.test.tsx`.

- [ ] **Step 1: Failing tests:** approving requires a confirm dialog stating it will create a real index in an isolated sandbox only; panel shows progress from events; renders before/after medians, p95, improvement %, index size, build time; warnings visible; regression is displayed as red "slower"; export buttons call the report URL; history lists past analyses with fingerprint, status and best validation state.
- [ ] **Step 2–5:** Run → FAIL; implement with Recharts (individual runs as dots, medians as bars/lines); PASS; commit `feat(web): benchmark panel, charts and history`.

### Task 25: Playwright end-to-end

**Files:** Create `apps/web/e2e/main-journey.spec.ts`, `apps/web/playwright.config.ts`; add `web` service to `compose.yaml`.

- [ ] **Step 1:** Write the journey: open sample project → pick catalog query 1 → run explain-only → open the Seq Scan node → inspect the critical finding → approve benchmark → see comparison with `after.median < before.median` → export report (Markdown download contains "Median"). Run → FAIL (before wiring).
- [ ] **Step 2–4:** Fix gaps until PASS against the full compose stack (`small` scale).
- [ ] **Step 5:** Commit `test(e2e): main user journey`.

### Task 26: Docs, hardening checks, final verification

**Files:** Create/Modify `README.md`, `docs/{architecture.md,security.md}`, `docs/rule-catalog.md`; adversarial tests `apps/api/tests/test_adversarial.py`.

- [ ] **Step 1: Failing adversarial tests:** multiple statements via API; DDL hidden after a comment; huge SQL (> 20k chars) 422; huge plan JSON truncated/limited without crashing; two concurrent benchmark approvals for one candidate create one run; query that times out ends `failed`; worker-crash simulation leaves no `bench_%` database after the janitor run (`benchmark.sandbox.drop_stale(max_age_s)` invoked at worker start).
- [ ] **Step 2–3:** Fix until PASS. Write README (architecture diagram, security decisions, benchmark methodology, how to run `docker compose up`, screenshots captured with browser tooling) and the two docs.
- [ ] **Step 4:** Run everything: `pytest` (unit + integration), `npm test`, `npm run build`, Playwright. Use superpowers:verification-before-completion; check each Definition-of-Done bullet from the spec §1 against evidence.
- [ ] **Step 5:** Commit `docs: README, architecture, security`; tag `v0.3.0`; request code review (superpowers:requesting-code-review) on the whole branch.
