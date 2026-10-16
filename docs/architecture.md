# Architecture

QueryDoctor is a modular monolith: one Python distribution holds all the analysis logic as pure, fixture-tested
functions, and thin applications (an API, a worker, a UI) wire it to HTTP, queues and a database.

```
packages/querydoctor/src/querydoctor/
  safety/       SQL validator (SQLGlot)                       no database access
  plan_parser/  raw EXPLAIN JSON -> PlanNode tree + metrics   pure
  diagnosis/    DiagnosisRule protocol, ten rules, engine     pure
  candidates/   usage extraction, generation, pruning,
                plan comparison                               pure
                hypopg.py: scoring                            needs a connection
  benchmark/    stats, reliability, report                    pure
                sandbox, checksum, runner                     needs the bench instance
  db/           guarded connections, explain, schema collect  needs a connection
  lab/          deterministic data generator, slow-query catalogs (PostgreSQL and MongoDB), demo traffic
  adapters/     DatabaseAdapter protocol, PostgresAdapter, MongoAdapter, registry
  mongo/        query model + validator, explain, plan normalization, rules, ESR candidates, schema, sandbox, benchmark, workload
  ranking/      candidate features, dataset, grouped splits, training, calibration, predictor, monitoring
  explain/      numbered facts, template explainer, claim validator, Ollama client
  netguard/     address classification and DNS pinning for custom connections
apps/api/       FastAPI routes, pipeline, Celery tasks, SQLAlchemy models
apps/web/       Next.js UI; the browser only ever talks to its own origin (/api is proxied)
```

Pure modules are tested with committed plan fixtures, so most of the logic needs no database to test.

## Two kinds of database

* **The QueryDoctor database** (`querydoctor-db`) stores projects, analyses, findings, candidates, benchmark runs and
  audit events. Analysis code paths can never reach it.
* **Target databases** are what is being investigated. `target-postgres` serves planner-only work (EXPLAIN and
  HypoPG) through a read-only role. `bench-postgres` hosts the throwaway clones used for benchmarks.

## Database adapters

The analysis pipeline, the benchmark job and the workload import never mention an engine. They ask
`get_adapter(engine)` for a `DatabaseAdapter` (`querydoctor.adapters.base`, a runtime-checkable protocol): `validate_query`,
`collect_schema`, `explain`, `normalize_plan`, `diagnose`, `generate_candidates`, `test_candidates`, `benchmark` and
`collect_workload`. `PostgresAdapter` wraps the existing functions; `MongoAdapter` wraps `querydoctor.mongo`. Both normalize
their plans into the same `ParsedPlan` / `PlanNode` tree (`ParsedPlan.engine` says which; MongoDB nodes carry
`docs_examined` / `keys_examined` and no estimates), so findings, the plan viewer, reports and explanations work on either.
`app/services/adapters.py` builds the adapter from the pipeline's replaceable functions, which is how tests substitute the
database. An analysis row records its `engine`; a workload snapshot does too, and the dashboard and its regression
comparison are scoped to one engine.

### MongoDB specifics

* **A query is a JSON spec**, not text for the server to parse. `validate_mongo` parses it into a structure (duplicate keys,
  unknown top-level keys, non-allowlisted stages, server-side JavaScript, `$out` / `$merge` anywhere, excessive depth or
  size are refused), substitutes `{"$param": n}` values, and the command is built *from the structure*
  (`mongo.explain.build_command`), never from a string. The fingerprint is taken from the shape with every literal replaced,
  so a profiler entry and an analysis of the same query share it, exactly as `pg_stat_statements` text does for PostgreSQL.
* **Plans** come from `explain` with `executionStats` (only when the person chose to execute) or `queryPlanner`. Slot-based
  execution wraps the logical plan, which is unwrapped; an aggregation's stages become a chain whose root is the last stage.
  Stage times are the server's own estimates, so the viewer shares time among stages rather than against the whole query.
* **Candidates** are built from the filter / sort structure by the ESR rule and shown as `db.<collection>.createIndex({...})`;
  the key list is kept structurally (`IndexCandidate.columns`) and the benchmark builds the index from that, never from the
  display string.
* **The sandbox** (`mongo/sandbox.py`) is a `bench_<12 hex>` database on the benchmark server. Slots are a TTL'd lock
  collection (two at a time; a crashed holder's slot is reclaimable), and stale sandboxes are removed by the janitor like
  PostgreSQL clones.
* **Result equality** uses server-side SHA-256 document digests, then a count and SHA-256 of the sorted
  digests in the worker. Only hashes leave the database. Duplicate multiplicity remains significant.

## Ranking model and explanations

`querydoctor.ranking` turns measured benchmarks into training data. Features are planner-only (59 of them, `FEATURE_VERSION`),
so the same vector exists when an idea is first suggested. Splits are by *query group*, never by row, so a model is judged on
shapes it has not seen. Heuristic, logistic and LightGBM models are compared with ranking metrics (NDCG@3, top-1 hit rate)
and a paired group-bootstrap interval; `use_for_ranking` becomes true only if the interval excludes zero, which on the
current data it does not (see [ml-evaluation.md](ml-evaluation.md)). A prediction is stored apart from the candidate's evidence
level and never changes it.

`querydoctor.explain` builds numbered facts from an analysis, renders a deterministic template over them, and optionally asks
a local Ollama model for a draft. The claim validator checks every number against a fact at the precision shown, requires a
fact reference per sentence and rejects SQL or DDL; a failed or slow model falls back to the template with the reason.

## Workload intelligence

`querydoctor.workload` is pure apart from one reader. `read_statements` selects the heaviest rows of
`pg_stat_statements` for the current database (the read-only role holds `pg_read_all_stats` so every role's text is
visible). `merge_statements` runs each text through the safety validator, keeps single table-reading `SELECT`s that are
not system-catalog plumbing, and groups them by the validator's **fingerprint**. pg_stat_statements' `$n` text
and a QueryDoctor analysis of the same query with literals share a fingerprint, which is what links the two worlds.
Counters add; the standard deviation is pooled (`sqrt(Σ n(σ² + μ²)/N − mean²)`); `p95_est_ms = mean + 1.645σ` is an
estimate and labelled so everywhere.

`analysis.py` holds the decisions: rank by total time, the seven panels, interval-based regression detection
(`recent mean = Δtotal / Δcalls` against the earlier cumulative mean; ignores resets and queries with fewer than 20 new
calls) and plan-change detection across analyses of one fingerprint. `app/services/workload.py` persists snapshots
(`workload_snapshots`, `workload_queries`; migration 0004) and builds the dashboard, per-query detail and per-fingerprint
history. The seed job resets statistics and runs `lab.workload`'s deterministic baseline traffic once.

## The analysis pipeline

| Stage | What happens | Event |
|---|---|---|
| Validate | single `SELECT`, no denied functions, parameter count checked | `validating_query` |
| Schema | row estimates, column types and indexes of the referenced tables | `collecting_schema` |
| Plan | `EXPLAIN (FORMAT JSON, VERBOSE, COSTS, SETTINGS)`, or with `ANALYZE, BUFFERS, WAL` if executing | `generating_plan` |
| Diagnose | ten rules over the normalized plan | `diagnosing` |
| Candidates | generate, prune redundant ones, test each with HypoPG, rank | `testing_candidates` |

Every exit path ends in `completed`, `failed` or `cancelled` with a matching event, so a client never waits on a job
that has finished. The candidate stage is optional: if it fails, the diagnosis is still delivered.

A benchmark is a second job (`benchmarking`, then one event per sandbox step, then `benchmark_completed`).

## Progress events

Workers publish events to Redis Streams under a key such as `analysis:12` or `benchmark:3`. The API relays them as
server-sent events. A subscriber that connects late replays the stream's history, and if the stream has expired the
endpoint closes with the state stored in the database. SSE fits because the browser only receives progress.

## Evidence levels

A candidate's `validation` is one of `suggested`, `planner_validated`, `benchmark_validated`.

* `planner_validated` requires the planner to *use* the hypothetical index and the change to be meaningful (at least
  10% estimated cost reduction, or a sequential scan replaced by an index scan, or a sort removed).
* `benchmark_validated` requires a completed benchmark with a median improvement of at least 10% (below that is
  indistinguishable from noise), matching result checksums (row order among ties on an `ORDER BY` key is ignored), the new
  index actually appearing in the indexed plan, no blocking warning, and clear separation: even the p95 of the indexed runs
  must beat the original median. (Relative variance alone does not veto: a 0.3 ms query has high CV even when every run
  is far faster than the original. The variance warning is still shown.)

The planner score is `0.35·cost + 0.25·rows read + 0.20·plan risk + 0.20·operator improvement` (each term 0–1) and is
documented as evidence, not proof.

## Design decisions worth knowing

* **PostgreSQL enforces "one statement", not just the validator.** Every user query runs through the extended protocol
  (`db/protocol.py`: Parse/Bind/Execute), which accepts exactly one statement, so text that fooled a parser still cannot run
  a second one. Parameters are bound as real protocol parameters, never quoted or interpolated.
* **What runs is what was validated.** The executed text is SQLGlot's canonical rendering of the parsed statement, and the
  validator refuses any query whose canonical form does not parse back to the identical tree (SQLGlot can print some
  constructs, such as an E-string with an escaped backslash, in a form PostgreSQL reads differently).
* **Row counts and times in plans are per loop**, as PostgreSQL reports them. Rules multiply by `loops` where totals matter,
  and finding evidence is always expressed in totals so numbers on one card agree with each other. Below a `Gather`, loops
  count processes, so elapsed time divides by the number of workers + the leader (`total_time_ms`).
* **Buffer and temp-block counts include children.** Spill rules use the `exclusive_temp_*` values, so an in-memory sort above
  a spilling join is not reported as a sort spill.
* **Candidates are tested with HypoPG on the explore instance and measured on the bench instance.** Hypothetical indexes
  influence only plain `EXPLAIN`, so the baseline for scoring is always a planner-only plan.
* **Benchmark concurrency is capped with Postgres advisory locks** on the bench instance, because several worker
  processes run at once and an in-process semaphore would cap nothing.
* **Double approval is safe.** A partial unique index allows at most one queued or running benchmark per candidate, so
  racing requests resolve in the database.
* **Layout is a tidy tree**, not a general graph layout, because a plan is always a tree and sibling order carries meaning.

## Data model

`workload_snapshots` and `workload_queries` (imported statistics, one row per fingerprint per snapshot, tagged with the engine),
`projects`, `connections` (passwords stored encrypted), `query_snapshots` (the canonical SQL and its parameters),
`analyses` (raw plan JSON, schema snapshot, status), `findings`, `recommendation_candidates` (including ruled-out ones
with their reason), `benchmark_runs` (config, result, status) and `audit_events`. Migrations live in
`apps/api/alembic`. The normalized plan tree is rebuilt from the stored raw plan when read.

## Ports

| Service | Host port |
|---|---|
| web | 3000 |
| api | 8010 (container 8000) |
| target-postgres | 5433 |
| bench-postgres | 5434 |
| querydoctor-db | 5435 |
| redis | 6379 |
| mongo | 27018 (container 27017) |
