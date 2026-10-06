# QueryDoctor v0.4–v0.7 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) or superpowers:subagent-driven-development. Steps use checkbox syntax. Every task is test-first: write the test, run it RED, implement, run it GREEN, commit.

**Goal:** Add workload intelligence, an ML recommendation ranker, grounded explanations with hardening, and MongoDB support on top of the shipped v0.3.

**Architecture:** New pure packages inside `querydoctor` (`workload`, `ranking`, `explain`, `netguard`, `mongo`, `adapters`); the API/worker/UI grow matching routes, tables (Alembic 0004–0007) and pages. The analysis pipeline becomes adapter-driven in v0.7 without changing PostgreSQL behaviour.

**Tech Stack:** adds scikit-learn, lightgbm, pandas, joblib, pymongo, httpx (runtime); `mongo:7` container; Ollama over local HTTP.

**Spec:** `docs/superpowers/specs/2026-10-06-querydoctor-v0.4-0.7-design.md`

## Global Constraints

- Evidence levels stay exactly `suggested`, `planner_validated`, `benchmark_validated`; ML output and explanations never change them.
- Host-side addresses use `127.0.0.1`; every published port binds to `127.0.0.1`; ports: web 3000, api 8010, target 5433, bench 5434, metadata 5435, redis 6379, mongo 27017.
- Every user query runs through the single-statement extended protocol (`db.protocol.execute_single`) and read-only sessions; Mongo queries use `maxTimeMS=10000` and a read-only user.
- No business rows are stored or returned. Numbers in generated prose must match a fact.
- Custom connections exist only when `QD_ALLOW_CUSTOM_CONNECTIONS=true`.
- Group-based splits only for ML evaluation; `use_for_ranking` is data-driven.
- Tests: pytest (unit + `@pytest.mark.integration` against the live lab + `@pytest.mark.chaos` run explicitly), Vitest, Playwright. The existing suites (404 backend, 86 frontend, E2E) must stay green after every task.

## Review Focus

1. `pg_stat_statements` rows with `$n` placeholders, non-SELECT text, QueryDoctor's own statements, or an empty view -> skipped and counted, never a crash; an empty import yields an empty snapshot (Tasks 2, 5, 6).
2. Model file missing, corrupt, or feature-name mismatch -> analysis completes exactly as before with heuristic ordering (Tasks 17, 18).
3. LLM output with invented numbers, unit tricks, SQL, or an unreachable/slow Ollama -> rejected or template fallback, never a 500 and never an unsupported statistic (Tasks 22, 23, 24).
4. Custom connection to loopback / link-local / metadata / private / rebinding hosts, flag off, or credential leakage in any response -> refused and never echoed (Tasks 26, 27).
5. Hostile MongoDB specs (`$where`, `$out`/`$merge` nested in `$lookup`/`$facet`, deep nesting, huge bodies) -> 422; the Mongo sandbox is always dropped on failure or cancel (Tasks 34, 39, 40).

---

# Slice v0.4 — Workload intelligence

### Task 1: Dependencies, grants, pgss visibility

**Files:** Modify `packages/querydoctor/pyproject.toml` (deps: `pandas`, `scikit-learn`, `lightgbm`, `joblib`, `pymongo`, `httpx`), `apps/api/pyproject.toml` (`httpx` runtime), `lab/seed.py` (post-load: `GRANT pg_read_all_stats TO qd_readonly` when connected as superuser; skip with a log line if not permitted), `infrastructure/docker/initdb/target/01_roles_and_extensions.sql` (same grant for fresh volumes); Test `tests/lab/test_seed_integration.py`.
**Produces:** installed libs in `.venv`; `qd_readonly` can read other roles' `pg_stat_statements` rows.

- [ ] **Step 1:** Test `test_readonly_role_can_read_pg_stat_statements_for_all_roles`: after `seed(admin)`, as `qd_readonly` `SELECT count(*) FROM pg_stat_statements` succeeds and `pg_has_role('qd_readonly','pg_read_all_stats','member')` is true. Run → FAIL.
- [ ] **Step 2:** `pip install` the libraries; implement the grant (idempotent) in `seed()`; apply the same grant now to the running container via `docker exec`. Run → PASS; run the full suite once to confirm nothing else moved.
- [ ] **Step 3:** Commit `chore: ML/Mongo dependencies and pg_read_all_stats grant`.

### Task 2: Statement model, merging, pgss reader

**Files:** Create `workload/{__init__,models,pgss}.py`; Test `tests/workload/test_pgss.py`.
**Produces:** `StatementRow(query, calls, total_ms, mean_ms, stddev_ms, min_ms, max_ms, rows, shared_hit_blocks, shared_read_blocks, temp_blocks)`; `WorkloadQuery(fingerprint, normalized_sql, sample_sql, calls, total_ms, mean_ms, stddev_ms, min_ms, max_ms, rows, shared_hit_blocks, shared_read_blocks, temp_blocks, p95_est_ms)`; `merge_statements(rows: list[StatementRow]) -> tuple[list[WorkloadQuery], int]` (queries, skipped count); `read_statements(conn, limit=500) -> list[StatementRow]`.

- [ ] **Step 1: Failing unit tests:** two rows whose queries differ only in constants/`$n` merge into one (calls and totals add, `mean == total/calls`, pooled stddev equals the population formula within 1e-9, min/max combine); non-SELECT (`INSERT`, `EXPLAIN …`, `SET`), unparsable text, and table-less statements (`SELECT $1`, `SELECT 1`) are skipped and counted; `p95_est_ms == mean + 1.645*stddev`; a pgss `$1` query and the same query with literals share one fingerprint; empty input -> `([], 0)`; zero-calls rows never divide by zero.
- [ ] **Step 2:** Implement with `validate_query` (fingerprint, tables); pooled variance from per-row (n, mean, stddev). Integration test (marked) `read_statements` returns rows from the lab after any query ran, ordered by total time.
- [ ] **Step 3:** Commit `feat(workload): pg_stat_statements reader and fingerprint merge`.

### Task 3: Workload analysis (pure)

**Files:** Create `workload/analysis.py`; Test `tests/workload/test_analysis.py`.
**Produces:** `rank(queries) -> list[RankedQuery]` (adds `share` of total, `rank`); `build_panels(queries, *, validated_fingerprints: set[str], regressions: list[Regression]) -> dict[str, list[RankedQuery]]` with keys `highest_total_time, slowest_p95, most_frequent, highest_io, most_unstable, largest_regression, missing_validation`; `Regression(fingerprint, baseline_mean_ms, recent_mean_ms, ratio, recent_calls)`; `detect_regressions(before, after, *, min_calls=20, min_ratio=1.5) -> list[Regression]`; `plan_changes(analyses: list[dict]) -> list[PlanChange]` (consecutive differing `plan_fingerprint`s with before/after operators).

- [ ] **Step 1: Failing tests (hand-computed):** a 50 ms × 50,000-call query outranks a 5,000 ms × 1-call query and shares sum to 1; panel membership/ordering for each panel; unstable requires calls >= 20 and CV = stddev/mean; interval maths `(total_b-total_a)/(calls_b-calls_a)` vs snapshot A's mean, ratio threshold boundary (1.49 no, 1.5 yes), `recent_calls < 20` ignored, fingerprints present in only one snapshot ignored, counter reset (calls_b < calls_a) ignored; `plan_changes` on [A,A,B,B,C] yields two changes with operators; division guards.
- [ ] **Step 2:** Implement; Step 3: commit `feat(workload): ranking, panels, regression and plan-change detection`.

### Task 4: Lab workload generator

**Files:** Create `lab/workload.py`; Test `tests/lab/test_workload.py` (unit: parameter sampling; integration: run + import).
**Produces:** `run_workload(dsn, scale, *, mode: Literal["baseline","regress"], seed=7, scale_calls=1.0) -> dict[str,int]` (calls per query id); `reset_statistics(admin_dsn)`; CLI `python -m querydoctor.lab.workload --dsn … [--admin-dsn … --reset] --mode baseline`.

- [ ] **Step 1: Failing tests:** sampled parameters are deterministic per seed and valid (customer ids exist, dates in range); `baseline` includes ≥ 3 cheap high-frequency queries with ≥ 10,000 calls total; integration: `reset_statistics` then `run_workload(mode="baseline")` then `read_statements` shows the cheap query with calls ≥ 10,000 and ranks (by `rank`) above the heavy catalog queries by total time; `regress` mode raises the interval mean of the customer-orders fingerprint ≥ 1.5× (verified via two imports and `detect_regressions`).
- [ ] **Step 2:** Implement using `execute_single` (extended protocol so pgss normalizes `$n`); heavy-customer params for `regress`. Step 3: commit `feat(lab): deterministic workload generator`.

### Task 5: Metadata tables and workload service

**Files:** Create `app/models/workload.py`, migration `0004_workload.py`, `app/services/workload.py`; Test `apps/api/tests/test_workload_service.py`.
**Produces:** `WorkloadSnapshot`, `WorkloadQueryRow` models; `import_snapshot(session, conn, project_id, limit=500) -> WorkloadSnapshot`; `latest_two(session, project_id)`; `dashboard(session, project_id, snapshot_id=None, compare_id=None) -> dict` (snapshot, ranked top 100, panels, regressions); `fingerprint_history(session, project_id) -> list[dict]`; `query_detail(session, project_id, fingerprint) -> dict`.

- [ ] **Step 1: Failing tests (SQLite, stub reader):** import persists snapshot + merged queries + skipped count; empty pgss -> snapshot with 0 queries; `dashboard` marks `missing_validation` using analyses with `benchmark_validated` candidates for that fingerprint; compare picks the previous snapshot; `fingerprint_history` groups analyses (first/last, count, mean and p95 of executed `execution_time_ms`, plan-change count, best validation); `query_detail` returns trend across snapshots, analyses and plan changes, and `sample` params when the fingerprint matches a catalog query.
- [ ] **Step 2:** Implement + `alembic revision --autogenerate`, apply to `querydoctor-db`. Step 3: commit.

### Task 6: Workload API

**Files:** Create `app/api/workload.py`; Modify `app/main.py`, `app/workers/tasks.py` (task `qd.run_demo_workload`); Test `apps/api/tests/test_workload_api.py`.
**Produces:** routes in spec §3.5; `PipelineDeps`-style `WorkloadDeps(target_dsn, reader, demo_runner)` for injection.

- [ ] **Step 1: Failing tests:** import returns 201 with counts; GET dashboard shape (panels keys, shares); unknown project 404; detail 404 for unknown fingerprint; `demo` returns 202 and invokes the runner with the mode (stub), invalid mode 422; fingerprints endpoint; reader failure (target down) -> 502 with a message, nothing persisted.
- [ ] **Step 2–3:** Implement; commit `feat(api): workload import, dashboard, detail and demo endpoints`.

### Task 7: Seed job runs a baseline workload; live stack tests

**Files:** Modify `lab/seed.py` CLI (`--with-workload`), `infrastructure/compose.yaml` (seed command), Test `apps/api/tests/integration/test_workload_stack.py`.

- [ ] **Step 1: Failing live test:** `POST /workload/import` on the stack -> ≥ 8 queries, the cheap high-frequency query is rank 1 by total time while a catalog query is slowest per call (the guide's prioritisation point), `regress` demo + second import -> a regression for the customer-orders fingerprint, `missing_validation` panel contains the unbenchmarked queries.
- [ ] **Step 2–3:** Wire seed (`reset` + baseline workload once, `--if-empty` aware), rebuild stack, run; commit.

### Task 8: Workload UI

**Files:** Create `app/workload/page.tsx`, `app/workload/[fingerprint]/page.tsx`, `components/workload/{Panels,QueryTable,RegressionList,ShareBar}.tsx`; Modify `lib/{api,types}.ts`, `app/layout.tsx` (nav), `app/projects/page.tsx` (fingerprint table), `components/AnalysisList.tsx` (link); Tests `__tests__/{QueryTable,Panels,RegressionList}.test.tsx`.

- [ ] **Step 1: Failing tests:** table shows rank, calls, mean, total, share bar (aria meter) and labels p95 as "estimated p95"; panel titles and ordering; regression list shows "was X ms, now Y ms (N× slower)"; row link goes to `/analyses/new?sql=…`/detail; empty snapshot shows an instructive empty state; import button calls the API and reloads.
- [ ] **Step 2–3:** Implement (reuse design tokens), browser-verify with Playwright screenshots; commit.

### Task 9: v0.4 verification gate

- [ ] Run all suites + build + E2E (add workload step to `e2e/workload.spec.ts`: import -> top query -> open detail); update README/architecture; tag `v0.4.0`; ledger entry.

---

# Slice v0.5 — ML recommendation ranker

### Task 10: Schema extras

**Files:** Modify `schema.py` (`TableInfo.size_bytes`, `write_ratio`, `column_stats: dict[str, ColumnStats]`; `ColumnStats(n_distinct_fraction, null_frac, correlation)`), `db/schema_collect.py` (`collect_schema(conn, tables, *, extras=False)`); Test `tests/db/test_schema_extras.py`.

- [ ] **Step 1: Failing integration tests:** with `extras=True` `orders` has `size_bytes > 1e6`, `write_ratio` in [0,1], `column_stats["customer_id"].n_distinct_fraction` in (0,1] and `created_at` |correlation| > 0.9 (sorted insert), nulls 0; `extras=False` leaves them `None`; negative `n_distinct` (fraction form) and absent stats handled.
- [ ] **Step 2–3:** Implement from `pg_class`, `pg_stat_user_tables`, `pg_stats`; commit.

### Task 11: Feature extraction

**Files:** Create `ranking/{__init__,features,descriptors}.py`; Test `tests/ranking/test_features.py`.
**Produces:** `FEATURE_VERSION = 1`; `FEATURE_NAMES: list[str]`; `FeatureInput(sql, usage, schema, original: ParsedPlan, candidate: IndexCandidate, candidate_plan: ParsedPlan | None, comparison: PlanComparison | None)`; `extract_features(fi) -> dict[str, float]` (all names present, finite); `DESCRIPTORS: dict[str, str]` feature -> plain phrase.

- [ ] **Step 1: Failing tests (fixtures):** names are unique/stable (golden list asserted); each group on the headline query (equality count 1, range 1, order 1, `has_select_star` 0, leading-column role equality, equality coverage 1.0, sort served 1, output coverage); join counts for the join catalog query; plan-difference features from the existing seq->index fixtures (removed seq scans 1, sort removed 0/1); planner-only plan without candidate plan gives zeros/NaN-free values; every value finite; schema extras missing -> neutral defaults.
- [ ] **Step 2–3:** Implement; commit.

### Task 12: Data variants

**Files:** Modify `lab/generator.py` (`COUNTS`/`ZIPF` gain `tiny, medium, skew_low, skew_high`; `generate(scale)` accepts them), `lab/seed.py` (choices), `infrastructure/docker/initdb/bench/*.sql` (`CREATE EXTENSION hypopg` in `template1` and the sample template); Create `lab/variants.py`; Tests `tests/lab/test_variants.py`.
**Produces:** `VARIANTS = ["tiny","small","medium","skew_low","skew_high"]`; `ensure_variant(admin_dsn, name) -> str` (creates and seeds `qd_variant_<name>` owned by `qd_bench` if missing; returns the template DB name); applies hypopg to the existing running bench instance.

- [ ] **Step 1: Failing tests:** counts per variant; `skew_high` top-1% share > `skew_low` top-1% share (+0.15); same seed deterministic; integration: `ensure_variant` idempotent, row counts right, `hypopg` extension present in a clone, no `bench_%` leak.
- [ ] **Step 2–3:** Implement; commit.

### Task 13: Query templates

**Files:** Create `lab/query_templates.py`; Test `tests/lab/test_query_templates.py`.
**Produces:** `QueryTemplate(id, sql, sampler: Callable[[Random, Variant], list])`; `TEMPLATES` (≥ 30 over orders/events/payments/order_items/customers/products: equality, range, join, sort, group, subquery, expression, `IN`, low/high selectivity); `sample_instances(variant, n_per_template, seed) -> list[QueryInstance(template_id, sql, params, query_id)]`.

- [ ] **Step 1: Failing tests:** every template validates (`validate_query`), param count matches, instances deterministic, ≥ 30 templates, fingerprints unique per template, integration: every template plannable (`explain`) on the small variant.
- [ ] **Step 2–3:** Implement; commit.

### Task 14: Shared-baseline candidate measurement

**Files:** Modify `benchmark/runner.py`; Test `tests/benchmark/test_measure_candidates.py`.
**Produces:** `measure_candidates(bench_dsn, sql, params, ddls: list[str], config, *, template, on_progress=…, cancelled=…) -> list[CandidateMeasurement(ddl, result: BenchmarkResult | None, error: str | None)]` — one clone, baseline measured once, then per DDL: create index -> ANALYZE -> measure -> checksum -> `DROP INDEX`; same safety rules (DDL validation, read-only measurement, teardown in `finally`, advisory slot).

- [ ] **Step 1: Failing integration tests:** three candidates (good, useless, invalid DDL) return results sharing one `before` (identical object values), the invalid one has `error`, the good one `improvement_pct > 20`, the useless `index_used False`; no leak; cancel mid-way tears down; baseline timeout.
- [ ] **Step 2–3:** Implement, refactoring `_measure_both` to reuse helpers; commit.

### Task 15: Dataset builder and background generation

**Files:** Create `ranking/dataset.py`, `lab/dataset_gen.py`; Test `tests/ranking/test_dataset.py`.
**Produces:** `DatasetRow` (feature dict + `log_speedup, improvement_pct, useful, regression, index_size_mb, build_ms, group_id, query_id, variant, template_id, ddl, baseline_ms, candidate_ms, planner_score, uses_candidate`); `write_rows/read_rows(path)`; `build_rows(variant_dsn_info, instances, config) -> Iterator[DatasetRow]`; decoy generator `decoys(usage, schema) -> list[IndexCandidate]`; CLI `python -m querydoctor.lab.dataset_gen --out datasets/ranker-v1.jsonl --variants … --per-template N`.

- [ ] **Step 1: Failing tests:** decoys are valid, differ from real candidates, include a wrong-order and an unrelated-column index; JSONL round trip; integration (tiny variant, 2 templates, 2 instances): rows have all feature names, finite labels, `useful` consistent with `improvement_pct >= 20`, group/query ids as specified, a useless decoy has `uses_candidate False` or `improvement_pct < 20`.
- [ ] **Step 2:** Implement; run a tiny live generation; **start the full generation in the background** (`--per-template` tuned for ~1,000 rows); Step 3: commit code (dataset committed when finished).

### Task 16: Metrics, splits, training

**Files:** Create `ranking/{metrics,split,train}.py`; Tests `tests/ranking/{test_metrics,test_split,test_train}.py`.
**Produces:** `precision_at_k(rel_by_query, scores_by_query, k)`, `ndcg_at_k(...)`, `top1_improves_rate`, `expected_calibration_error(y, p, bins=10)`, `brier(y, p)`, `reliability_points(y, p, bins)`; `group_holdout(groups, frac, seed)`, `group_kfold(groups, k)`; `train_all(rows, *, seed) -> TrainedBundle` with heuristic baseline, logistic, LGBM regressor/classifier/size/regression models, LGBMRanker, isotonic calibrator, `meta` (per-model held-out + CV metrics, importances, calibration points, data hash, `use_for_ranking`, reason); `save_bundle(path)` / `load_bundle(path)` (joblib + meta json).

- [ ] **Step 1: Failing tests:** metrics against hand-computed tiny cases (NDCG@3 = 0.9 example, ECE of a perfectly calibrated set ≈ 0, constant-probability case); splits never put a group on both sides (property test over random groups); training on a synthetic dataset with a planted signal beats the heuristic baseline on held-out groups and sets `use_for_ranking` true; on pure noise `use_for_ranking` is false; deterministic given the seed; bundle save/load round trip preserves predictions.
- [ ] **Step 2–3:** Implement; commit.

### Task 17: Predictor and contribution explanations

**Files:** Create `ranking/predictor.py`; Test `tests/ranking/test_predictor.py`.
**Produces:** `Predictor.load(dir) -> Predictor | None` (None on missing/corrupt/version-or-feature mismatch, logged, never raising); `predict(features: dict) -> Prediction(expected_speedup, p_improve, expected_index_mb, p_regression, rank_score, contributions: list[Contribution(feature, value, effect, phrase)], model_version)`; `Predictor.use_for_ranking`.

- [ ] **Step 1: Failing tests:** missing dir/file, truncated joblib, wrong feature list, and `FEATURE_VERSION` mismatch each give `None`; a valid bundle predicts in range (`p_improve` in [0,1], speedup > 0); contributions are the top 3 by |effect|, mapped through `DESCRIPTORS`, and sum (with the base value) to the raw model output within 1e-6; prediction never raises on NaN input (neutral fill).
- [ ] **Step 2–3:** Implement (`pred_contrib=True`); commit.

### Task 18: Pipeline integration, feedback, outcomes, model API

**Files:** Create `app/models/ranking.py` (`ModelPrediction`, `UserFeedback`, `ModelOutcome`), migration `0005_ranking.py`, `app/services/ranking.py`, `app/api/model.py`; Modify `app/services/candidates.py` (extract features after scoring; call predictor; reorder when `use_for_ranking`), `app/services/benchmarks.py` (record outcome), `app/schemas/analysis.py` (`CandidateOut.prediction`), `app/main.py`; Tests `apps/api/tests/test_ranking_integration.py`.
**Produces:** `PipelineDeps.predictor: Predictor | None`; `POST /api/analyses/{id}/candidates/{cid}/feedback {helpful, comment?}`; `GET /api/model` (meta); `GET /api/model/monitor`.

- [ ] **Step 1: Failing tests (stub predictor + fake scorer):** predictions persisted per kept candidate and exposed as `prediction` (separate from `validation`); `use_for_ranking` false keeps planner-score order, true reorders by `rank_score`; evidence badge/validation unchanged either way; predictor `None`, raising, or returning NaN -> analysis completes with no predictions; benchmark completion records an outcome (actual vs predicted, abs error) and `monitor` reports n/MAE/precision/calibration bins/drift flag (drift only at n ≥ 20 and MAE > 1.5× training MAE); feedback validated and idempotent per (candidate, session); `GET /api/model` returns meta or `{"available": false}`.
- [ ] **Step 2–3:** Implement + migration; commit.

### Task 19: Prediction UI and models page

**Files:** Create `components/candidates/PredictionBlock.tsx`, `components/candidates/FeedbackButtons.tsx`, `app/models/page.tsx`, `components/models/{MetricsChart,ImportanceChart,CalibrationChart}.tsx`; Modify `CandidateCard.tsx`, `lib/{api,types}.ts`, `layout.tsx`; Tests `__tests__/{PredictionBlock,ModelsPage}.test.tsx`.

- [ ] **Step 1: Failing tests:** block shows "Prediction" heading distinct from the evidence badge, "~N× faster", probability, index size, regression risk, top-3 contributions in plain phrases, and says it is not a measurement; absent prediction renders nothing; feedback buttons post and disable; models page renders per-model metric table, marks the deployed ranker and `use_for_ranking` reason, handles `available:false` with an instructive message.
- [ ] **Step 2–3:** Implement; browser-verify; commit.

### Task 20: Train on the real dataset; v0.5 gate

- [ ] When generation finishes: commit `datasets/ranker-v1.jsonl`; run `python -m querydoctor.ranking.train` -> `models/ranker.joblib` + `ranker.meta.json`; read the evaluation honestly into `docs/ml-evaluation.md` (including the case where the heuristic wins); mount `models/` into the api/worker images; full suites + E2E; screenshots; tag `v0.5.0`.

---

# Slice v0.6 — Grounded explanations, hardening, chaos

### Task 21: Evidence package

**Files:** Create `explain/{__init__,facts}.py`; Test `tests/explain/test_facts.py`.
**Produces:** `Fact(id, label, value: float|int|str, unit: str|None, text: str)`; `EvidencePackage(kind, facts: list[Fact])` with `by_id`, `numeric_values()`; `build_evidence(analysis: dict, kind: str) -> EvidencePackage`.

- [ ] **Step 1: Failing tests:** facts ids are `F1..Fn` stable; every number comes from the input dict (property: each fact value is findable in the analysis JSON or a documented derivation: percent, ratio); planner-only analysis omits timing facts; benchmark facts present only when completed; kinds select the relevant subset; no fact contains row data or connection strings.
- [ ] **Step 2–3:** Implement; commit.

### Task 22: Template explainer and claim validator

**Files:** Create `explain/{template,validator}.py`; Tests `tests/explain/{test_template,test_validator}.py`.
**Produces:** `Explanation(text, sentences: list[Sentence(text, fact_ids)], source, dropped: list[str], fallback_reason: str|None)`; `explain_template(pkg) -> Explanation`; `validate_claims(text, facts) -> ValidationResult(ok, kept_text, dropped, reason)`.

- [ ] **Step 1: Failing tests:** every template sentence cites ≥ 1 fact and passes the validator; validator table: supported (`742 ms`, `94.5%`, `1.8M` for 1,824,403, `8x` for ratio 7.97, thousands separators), unsupported (`99%` when fact is 94.5, `10x`), identifiers ignored (`customer_id`, `t1`, `$1`, `PG_SEQ_SCAN_FILTER_WASTE`), unit confusion (`742 s` vs `742 ms` rejected), unicode digits/fullwidth digits normalized, sentences dropped individually, rejection when nothing survives / > 50% dropped / DDL keywords appear (`CREATE`, `DROP`, `ALTER`, `TRUNCATE`), tolerance 1% only at displayed precision; deterministic.
- [ ] **Step 2–3:** Implement; commit.

### Task 23: Ollama explainer and orchestration

**Files:** Create `explain/{ollama,service}.py`; Test `tests/explain/test_ollama.py` (fake HTTP server via `http.server` thread).
**Produces:** `OllamaExplainer(url, model, timeout_s=60)`; `ExplainerUnavailable`; `explain(analysis: dict, kind, *, ollama: OllamaExplainer | None) -> Explanation` (states `build_evidence -> draft -> validate`; any failure falls back to the template with `fallback_reason`).

- [ ] **Step 1: Failing tests (fake server):** prompt contains the facts JSON and the rules; a faithful reply is accepted (`source == "ollama:<model>"`); invented-number sentences are dropped; fully invented reply -> template fallback with reason; server down, timeout, HTTP 500, malformed JSON, empty reply, model missing -> fallback, never an exception; model empty/None -> template without a network call; reply with SQL/DDL rejected.
- [ ] **Step 2–3:** Implement (`httpx`, `/api/chat`, `stream:false`, temperature 0.2); commit.

### Task 24: Explanation API, worker task, UI

**Files:** Create `app/models/explanation.py`, migration `0006_explanations.py`, `app/api/explanations.py`, task `qd.run_explanation`; Modify `config.py` (`ollama_url`, `ollama_model`), compose env (`host.docker.internal`); Create `components/explain/ExplainPanel.tsx`; Tests `apps/api/tests/test_explanations_api.py`, `__tests__/ExplainPanel.test.tsx`.

- [ ] **Step 1: Failing tests:** POST queues and (in-process) completes; GET returns text, source, citations/facts, dropped sentences; invalid kind 422; unknown analysis 404; review endpoint stores accepted/rejected; works with the Ollama client stubbed to fail (template); UI renders five kinds, the source badge, facts list, copy button for the PR comment, "Template" vs "Ollama · model, validated", polling until done, error state.
- [ ] **Step 2–3:** Implement; browser-verify; commit.

### Task 25: Network guard

**Files:** Create `netguard/{__init__,guard}.py`; Test `tests/netguard/test_guard.py`.
**Produces:** `GuardConfig(allow_private=False, allowed_ports={5432}, allowed_names={sample hosts})`; `resolve_and_check(host, port, config, *, resolver=socket.getaddrinfo) -> ResolvedTarget(host, ip, port)`; `NetworkBlocked(reason)`.

- [ ] **Step 1: Failing tests (injected resolver):** blocked: `127.0.0.1`, `::1`, `localhost`, `0.0.0.0`, `169.254.169.254`, `fe80::1`, `10.1.2.3`, `172.16.0.1`, `172.31.255.255`, `192.168.1.1`, `100.64.0.1`, `fc00::1`, `::ffff:127.0.0.1`, `::ffff:10.0.0.1`, multicast, decimal/hex/octal IP spellings (`2130706433`, `0x7f000001`, `0177.0.0.1`), a name resolving to several IPs where one is private, ports 22/6379/3306; allowed: public IP (`93.184.216.34`), public name; private allowed only with `allow_private`; allowlisted sample names pass; rebinding: the returned `ip` is the one checked and a second resolver call with a different answer is never used.
- [ ] **Step 2–3:** Implement with `ipaddress`; commit.

### Task 26: Connection registry, use in analyses, privacy, deletion

**Files:** Modify `app/models/project.py` (`Connection` gains `sslmode`, `deleted_at`), migration `0007a`? (include in 0006), Create `app/api/connections.py` additions, `app/api/privacy.py`; Modify `app/services/pipeline.py` (connection-aware DSN via guard + `hostaddr`), `app/api/analyses.py` (`connection_id`, `DELETE /api/analyses/{id}`), `config.py` (`allow_custom_connections`, `allow_private_networks`); Tests `apps/api/tests/{test_connections_registry,test_privacy,test_delete_analysis}.py`.

- [ ] **Step 1: Failing tests:** flag off -> create/list return 403 with a clear message; flag on: create stores an encrypted password (DB value ≠ plaintext, decrypts), responses never contain the password or ciphertext; SSRF-blocked hosts 400 with the reason class (not echoing resolver internals); delete wipes the secret and writes audit events `connection_created/deleted`; analysis with `connection_id` connects to the resolved IP (stub connector asserts `hostaddr`) and records `connection_used`; benchmark on a custom-connection analysis -> 409; HypoPG unavailable -> candidates `suggested` with a note; `GET /api/privacy` lists stored items; `DELETE /api/analyses/{id}` cascades (findings, candidates, runs, predictions, explanations) and audits; unknown/foreign ids 404.
- [ ] **Step 2–3:** Implement; commit.

### Task 27: Log redaction

**Files:** Create `app/logging_utils.py`; Modify `app/main.py`, `app/database.py` (`hide_parameters=True`), `workers/celery_app.py`; Test `apps/api/tests/test_redaction.py`.

- [ ] **Step 1: Failing tests:** `redact("SELECT * FROM t WHERE email = 'a@b.c' AND id = 42")` -> literals replaced by `?`, identifiers preserved; filter attached to root logger redacts `record.msg/args` including nested tuples and psycopg/SQLAlchemy messages; a raised DB error logged through `logging.exception` contains no literal; engines created with `hide_parameters`.
- [ ] **Step 2–3:** Implement; commit.

### Task 28: Connections and privacy UI

**Files:** Modify `app/settings/page.tsx`, `app/analyses/new/page.tsx`, `lib/{api,types}.ts`, `components/AnalysisResult.tsx` (delete button + custom-connection benchmark note); Create `components/settings/{ConnectionsPanel,PrivacyPanel}.tsx`; Tests `__tests__/{ConnectionsPanel,PrivacyPanel}.test.tsx`.

- [ ] **Step 1: Failing tests:** flag-off message; add-connection form posts without echoing the password afterwards; delete confirmation; privacy list rendered from the API; New analysis shows a connection selector only when connections exist; delete-analysis confirmation navigates away.
- [ ] **Step 2–3:** Implement; commit.

### Task 29: Chaos tests

**Files:** Create `apps/api/tests/chaos/test_chaos.py` (`@pytest.mark.chaos`), `pytest.ini` marker + `-m "not chaos"` default via `addopts`.

- [ ] **Step 1: Write and run explicitly:** (a) lock contention: a superuser session holds `ACCESS EXCLUSIVE` on `customers`; an executed analysis ends `failed` with a timeout/lock message in < 10 s and the stack stays up; (b) `docker restart` of target-postgres during a long executed analysis: analysis ends `failed`, the next analysis succeeds; (c) kill the worker container during a benchmark, restart it: the stuck run becomes `failed` (janitor) and no `bench_%` database remains; (d) the Ollama endpoint hanging -> explanation falls back to template within the timeout.
- [ ] **Step 2–3:** Fix whatever they reveal (test-first); commit.

### Task 30: v0.6 gate

- [ ] Full suites + E2E for explanations (template path) + docs (`docs/security.md` network guard table, README, ledger); tag `v0.6.0`.

---

# Slice v0.7 — MongoDB

### Task 31: Adapter protocol and PostgresAdapter

**Files:** Create `adapters/{__init__,base,postgres}.py`, migration `0007_engine.py` (`engine` on `analyses`, `query_snapshots`, `workload_snapshots`), Modify `app/services/{pipeline,candidates,benchmarks}.py` to call adapters, `plan_parser/models.py` (`ParsedPlan.engine="postgres"`, `PlanNode.docs_examined`, `keys_examined`); Tests `tests/adapters/test_postgres_adapter.py` + the entire existing suite.
**Produces:** `DatabaseAdapter` Protocol (`engine`, `validate_query(text, params) -> ValidatedQuery`, `collect_schema(conn, vq)`, `explain(conn, vq, params, execute)`, `normalize_plan(raw)`, `diagnose(ctx)`, `generate_candidates(vq, schema, findings)`, `test_candidates(conn, vq, params, plan, cands, schema)`, `benchmark(...)`, `collect_workload(conn, limit)`); `get_adapter(engine)`.

- [ ] **Step 1: Failing tests:** `PostgresAdapter` satisfies the protocol (runtime-checkable) and delegates identically (explain/diagnose/candidates produce the same results as the direct functions on fixtures); `get_adapter("mongodb")` raises until registered; `ParsedPlan.engine` defaults.
- [ ] **Step 2:** Implement as thin wrappers and switch the services to adapters. **The whole existing suite (unit + live) must pass unchanged.** Step 3: commit.

### Task 32: Mongo service, users, settings

**Files:** Modify `infrastructure/compose.yaml` (`mongo` service `127.0.0.1:27017`, init script), Create `infrastructure/docker/initdb/mongo/01_users.js`, `config.py` (`mongo_target_uri`, `mongo_bench_uri`), `.env.example`; Test `tests/mongo/test_connectivity_integration.py`.

- [ ] **Step 1: Failing integration tests:** read-only user can read `qd_sample` and cannot write; bench user can create/drop `bench_*` databases; neither is a root user; `maxTimeMS` is enforced on a slow command.
- [ ] **Step 2–3:** `docker compose up -d mongo` (pulls the image), implement; commit.

### Task 33: Mongo query model and validator

**Files:** Create `mongo/{__init__,query,validator}.py`; Test `tests/mongo/test_validator.py`.
**Produces:** `MongoQuery(kind: "find"|"aggregate", collection, filter, sort, projection, limit, pipeline)`; `validate_mongo(text: str, params: list, *, max_bytes=50_000, max_depth=20) -> ValidatedMongoQuery(query, canonical_json, fingerprint, collections, param_count, shape)`; `MongoValidationError(code, message)`; `normalize_shape(query) -> str` (literals -> `?`).

- [ ] **Step 1: Failing tests:** accepts find/aggregate with `{"$param": n}` placeholders (count checked, scalars only, substituted deterministically); rejects with codes: `$where`, `$function`, `$accumulator`, `mapReduce`, `$out`/`$merge` at any depth including inside `$lookup.pipeline` and `$facet`, `$currentOp`/`$listSessions`/`$planCacheStats`, unknown top-level keys, non-allowlisted stages, regex `$regex` with catastrophic flags excluded? (length cap), depth > 20, > 50 KB, invalid JSON, duplicate keys, non-object; same shape fingerprint for different literals; stage allowlist (`$match,$group,$sort,$limit,$skip,$project,$unwind,$lookup,$count,$addFields,$set,$unset,$facet,$bucket,...`).
- [ ] **Step 2–3:** Implement; commit.

### Task 34: Mongo sample data, indexes, profiler, catalog

**Files:** Create `lab/mongo_seed.py`, `lab/mongo_catalog.py`; Tests `tests/mongo/test_seed.py`, `tests/mongo/test_catalog.py`.
**Produces:** `seed_mongo(uri, scale, *, db="qd_sample")` (customers, orders with embedded `items`/`payment`, events; deliberate indexes incl. redundant pair on `events`; profiler level 2); `MONGO_CATALOG` (8 `SampleQuery`-shaped entries with `engine="mongodb"`, JSON `sql`, expected rule ids and candidate); seed job wiring.

- [ ] **Step 1: Failing tests:** deterministic counts/skew parity with the SQL lab, embedded structure, the intended indexes (and only those), every catalog query validates; integration: each catalog query explains with the expected stage (COLLSCAN/SORT) per the table in spec §6.5.
- [ ] **Step 2–3:** Implement + seed job + compose; commit.

### Task 35: Mongo explain and plan normalization

**Files:** Create `mongo/{explain,plan}.py`, fixtures `fixtures/mongo_plans/*.json` (captured live); Tests `tests/mongo/test_plan.py`.
**Produces:** `explain_query(db, vq, params, *, execute: bool) -> dict`; `normalize_plan(raw) -> ParsedPlan(engine="mongodb")`.

- [ ] **Step 1: Failing tests (fixtures):** COLLSCAN->FETCH->SORT tree maps to `PlanNode`s with ids, `actual_rows = nReturned`, `docs_examined`, `keys_examined`, exclusive time by subtraction, `estimated_*` zero; `inputStages` (OR/merge), aggregation `$cursor` stage, sharded shape `SINGLE_SHARD`; missing fields tolerated; executionStats absent (queryPlanner only) -> `executed False`; integration: live explain of each catalog query returns a parseable plan within the time limit; hostile timeouts raise a typed error.
- [ ] **Step 2–3:** Implement; capture fixtures with a script; commit.

### Task 36: Mongo rules

**Files:** Create `mongo/rules/*.py` (10 rules), registry `MONGO_RULES`, `docs/rule-catalog.md` section; Test `tests/mongo/test_rules.py`.
**Produces:** rules `MONGO_COLLSCAN, MONGO_SCAN_AMPLIFICATION, MONGO_KEY_AMPLIFICATION, MONGO_BLOCKING_SORT, MONGO_INDEX_ORDER, MONGO_UNBOUNDED_AGG, MONGO_LARGE_LOOKUP, MONGO_UNSELECTIVE_INDEX, MONGO_REDUNDANT_INDEX, MONGO_STAGE_ORDER`; `run_mongo_diagnosis(context) -> list[Finding]`; `MongoContext(plan, query, collections: dict[str, CollectionInfo(count, indexes)], thresholds)`.

- [ ] **Step 1: Failing tests (positive and negative per rule, boundary values; scan amplification = docsExamined/max(nReturned,1) e.g. 500,000/20 = 25,000; never-divide-by-zero; planner-only plans silent or low-confidence):** plus every rule never crashes on every committed fixture.
- [ ] **Step 2–3:** Implement; commit.

### Task 37: Mongo candidates

**Files:** Create `mongo/candidates.py`; Test `tests/mongo/test_candidates.py`.
**Produces:** `generate_mongo_candidates(vq, collections, findings) -> list[IndexCandidate]` (ESR ordering, `ddl` = display string `db.orders.createIndex({"customer_id": 1, "created_at": -1})`, `columns` carry direction); `prune_mongo_redundant(cands, collections)`; `build_create_index(candidate) -> (keys: list[tuple[str,int]])` (structure, never parsed from the string).

- [ ] **Step 1: Failing tests:** equality -> sort -> range ordering (for `{status: "x", created_at: {$gte: …}}` sort `created_at:-1`), `_id` and existing-prefix redundancy dropped, compound-order fix candidate for MONGO_INDEX_ORDER, aggregation `$match` prefix used, caps on key count, `validation == "suggested"`.
- [ ] **Step 2–3:** Implement; commit.

### Task 38: Mongo sandbox benchmark

**Files:** Create `mongo/{sandbox,benchmark}.py`; Tests `tests/mongo/test_benchmark_integration.py`.
**Produces:** `MongoSandbox(bench_uri, source_db).create(collections) / drop()`; `run_mongo_benchmark(bench_uri, vq, params, candidate, config, *, on_progress, cancelled) -> BenchmarkResult` (same model as PostgreSQL; `index_size_bytes` from `collStats`/`$indexStats`, `index_used` from the winning plan, `checksum` via the server-side count + `$mod($toHashedIndexKey)` sum).

- [ ] **Step 1: Failing tests:** the headline Mongo query measurably faster after the real index (≥ 20%), checksum equal, nothing left behind (no `bench_%` db) on success, failure, cancel and timeout, key structure used (never a string), hostile candidate rejected, advisory cap (max 2 concurrent via a lock collection with TTL), read paths never write into the source db.
- [ ] **Step 2–3:** Implement; commit.

### Task 39: MongoAdapter, pipeline wiring, API, golden tests

**Files:** Create `adapters/mongo.py`; Modify `adapters/__init__.py`, `app/api/{analyses,samples,projects}.py` (`engine` field, per-engine samples `GET /api/sample-queries?engine=`; seed the Mongo project), `app/main.py`, `services/{pipeline,candidates,benchmarks}.py` (engine routing; Mongo has no HypoPG stage); Tests `apps/api/tests/{test_mongo_api.py}`, `apps/api/tests/integration/test_mongo_golden.py`.

- [ ] **Step 1: Failing tests:** an analysis with `engine="mongodb"` completes end to end with stubbed adapter pieces; hostile specs 422 with codes (`$where`, nested `$out`, deep, huge, bad JSON); mixed history/lists show engine; benchmark approval routes to the Mongo runner; **live golden tests:** each of the 8 Mongo queries yields its expected `MONGO_*` finding and (where defined) its expected index, which a real benchmark validates (`benchmark_validated`), with no leftovers.
- [ ] **Step 2–3:** Implement; rebuild stack; commit.

### Task 40: Mongo workload

**Files:** Create `mongo/workload.py`; extend `lab/workload.py` (Mongo traffic), `app/services/workload.py` (engine-aware import); Test `tests/mongo/test_workload.py`.

- [ ] **Step 1: Failing tests:** `system.profile` entries normalize to shapes (same shape for different literals), grouped counts/means/totals, internal/system commands excluded, empty profile -> empty snapshot, API import with `engine=mongodb`; live: after the Mongo demo traffic the cheap high-frequency query ranks first by total time.
- [ ] **Step 2–3:** Implement; commit.

### Task 41: Mongo UI

**Files:** Modify `components/query-editor/{QueryEditor,SqlEditor,SamplePicker}.tsx`, `app/analyses/new/page.tsx`, `components/plan-viewer/PlanNodeBody.tsx` (docs/keys for Mongo, hide "est" when engine is mongodb), `components/candidates/CandidateCard.tsx`, `components/AnalysisList.tsx` (engine badge), `app/workload/page.tsx` (engine filter), `lib/{api,types}.ts`; Tests extend `__tests__/{QueryEditor,PlanNodeBody,...}`.

- [ ] **Step 1: Failing tests:** engine selector switches editor language (JSON), samples and the explain/execute warning copy; node card shows "examined 500k · returned 20" and amplification for Mongo and no "est"; DDL shown verbatim; badge in lists; workload engine filter; PostgreSQL behaviour unchanged.
- [ ] **Step 2–3:** Implement; browser-verify both engines; commit.

### Task 42: Mongo E2E, docs, final verification

- [ ] Playwright journey for MongoDB (select sample -> execute -> plan -> finding -> approve benchmark -> faster -> export); update README (scope, ports, ML evaluation link, Ollama setup), `docs/architecture.md` (adapters, ranking, explain), `docs/security.md` (netguard, mongo validator, redaction, privacy), `docs/rule-catalog.md` (Mongo rules); regenerate screenshots (workload, models, explain, mongo).
- [ ] Run every suite (backend incl. integration, `-m chaos`, vitest, tsc, eslint, build, all Playwright specs) on a quiet machine, five consecutive full backend runs; check each Definition-of-Done bullet from both specs with evidence; tag `v0.7.0`.
- [ ] Final whole-branch review (fresh reviewer), one fix pass test-first, merge to `master` locally, preserve the ledger.
