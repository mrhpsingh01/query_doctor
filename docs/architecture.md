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
  lab/          deterministic data generator, slow-query catalog
apps/api/       FastAPI routes, pipeline, Celery tasks, SQLAlchemy models
apps/web/       Next.js UI; the browser only ever talks to its own origin (/api is proxied)
```

Pure modules are tested with committed plan fixtures, so most of the logic needs no database to test.

## Two kinds of database

* **The QueryDoctor database** (`querydoctor-db`) stores projects, analyses, findings, candidates, benchmark runs and
  audit events. Analysis code paths can never reach it.
* **Target databases** are what is being investigated. `target-postgres` serves planner-only work (EXPLAIN and
  HypoPG) through a read-only role. `bench-postgres` hosts the throwaway clones used for benchmarks.

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
  index actually appearing in the indexed plan, and no blocking or high-variance warning.

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
