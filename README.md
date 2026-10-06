# QueryDoctor

**An evidence-based PostgreSQL and MongoDB performance investigator.** Give it a slow `SELECT` (or a MongoDB `find` /
`aggregate`). It reads the execution plan, finds the bottleneck, tests index ideas without creating them where the
database allows it, benchmarks the promising ones in an isolated copy of the data, and recommends only what the
measurements support.

> The slow query took 8 ms because PostgreSQL scanned all 100,000 orders and threw away 97.2% of them. A composite
> index on `(customer_id, created_at)` turns the sequential scan into an index scan. Across 7 runs the median fell to
> 0.32 ms (95.8% faster), with identical output.

That sentence is generated from measurements, not from a language model guessing. A local model (Ollama) may optionally
draft explanations, but every number it writes is checked against the measurements and a deterministic template is the
fallback; see [Explanations](#explanations-your-own-databases-privacy). No model ever changes an evidence level.

![Plan viewer with the slowest operator selected](docs/screenshots/02-plan-and-operator.png)

## Try it

You need Docker. From the repository root:

```bash
docker compose -f infrastructure/compose.yaml up -d --build
```

| What | Where |
|---|---|
| Web app | <http://localhost:3000> |
| API (OpenAPI docs at `/docs`) | <http://localhost:8010> |
| Sample / benchmark PostgreSQL, metadata database, Redis | `127.0.0.1` ports 5433 / 5434 / 5435 / 6379 |
| MongoDB 7 (sample and benchmark copies) | `127.0.0.1:27018` (`QD_MONGO_PORT`; 27018 so a MongoDB already on 27017 is left alone) |

The first start builds the images, creates three PostgreSQL 16 instances, MongoDB 7 and Redis, and loads a deterministic,
skewed e-commerce dataset into both PostgreSQL and MongoDB (100k orders by default). Open the web app, pick **Recent
orders for one customer**, choose **Execute with analysis**, and follow the journey below. Choose **MongoDB** on the
New analysis page for the MongoDB catalog.

Use the larger demo dataset (1M orders, 3M order items, 5M events) with `QD_SCALE=full`, and set `QD_API_PORT` /
`QD_WEB_PORT` / `QD_MONGO_PORT` if a default port is taken on your machine.

## The journey

1. **Pick or paste a slow query.** Eight curated ones ship with the sample shop. Nothing runs against the database
   until you submit.
2. **Read the plan.** Operators are laid out with the result on top. Each carries a thin bar showing how much of the
   query's time that step spent itself, so the bottleneck is visible before you read a number.
3. **See the evidence.** Every finding cites the measurements that triggered it and says what might help.
4. **Get index ideas, labelled by how well they are backed.**

   | Label | Meaning |
   |---|---|
   | Suggested | Derived from the query's shape. Not tested. |
   | Planner-validated | PostgreSQL's planner picks the index in a what-if test (HypoPG). Not measured. |
   | Benchmark-validated | Measured faster, with identical output, in an isolated copy of the database. |

5. **Approve a benchmark** (an explicit confirmation is required), watch it run, and compare medians, p95, index size
   and build time.
6. **Export the report** as Markdown or standalone HTML.

![An index idea with its evidence level, then the measured comparison](docs/screenshots/05-benchmark.png)

## MongoDB

Choose **MongoDB** on the New analysis page: the editor switches to JSON, and eight curated slow queries over the same
shop (orders, customers, events with embedded items and payments) load as samples. A query is one `find` or `aggregate`
as JSON, with `{"$param": 1}` marking values:

```json
{"find": "orders", "filter": {"customer_id": {"$param": 1}}}
```

* **Validated before anything runs.** Only reads: stages are allowlisted, `$where` / `$function` / `$accumulator` /
  `$out` / `$merge` are refused at any depth (including inside `$lookup` sub-pipelines), and depth, size, limit and
  regex length are capped. Commands carry `maxTimeMS` and run as a read-only user.
* **Plans are measured, not estimated.** MongoDB has no cost model, so a plan node shows *documents examined, documents
  returned* and the amplification ("500k examined for 20 returned: 25k× per result"), never an estimate.
* **Ten MongoDB rules** ([catalog](docs/rule-catalog.md#mongodb-rules)): collection scans, scan and key amplification,
  blocking sorts, compound-index field order, unbounded `$group`, `$lookup` into unindexed fields, unselective and
  redundant indexes, and a `$match` that could run earlier.
* **Index ideas follow the ESR rule** (equality, then sort, then range) and are shown as the command you would run:
  `db.orders.createIndex({"customer_id": 1})`. MongoDB has no hypothetical indexes, so an idea stays *Suggested* until a
  benchmark measures it.
* **Benchmarks run in a throwaway database** (`bench_<12 hex>`) on the same server: the touched collections and their
  existing indexes are copied with `$out`, the real index is built there, and the query is timed before and after. The
  result checksum is computed inside the database (a count and an order-insensitive hash), so no document leaves it.
  The sandbox is always dropped, and at most two run at once. The sample data is only ever read.
* **Workload** reads the profiler (`system.profile`, level 2 in the sample) and ranks query shapes the same way. The
  profiler records whole milliseconds, so a sub-millisecond query shows 0 ms and cannot top the ranking however often
  it runs; that is a limit of the profiler, not hidden.

![A MongoDB plan: documents examined against returned](docs/screenshots/11-mongo-plan.png)

## Which query should I fix first? (Workload)

A single slow query is only half the question. The **Workload** page imports the database's own statistics
(`pg_stat_statements` for PostgreSQL, the profiler for MongoDB; a selector switches between them), merges statements that
are the same query shape, and ranks them by **total time** (mean × calls), because a 6 ms query called 1,500 times costs
more than a 600 ms query called once.

* Seven panels: highest total time, slowest (estimated) p95, most frequent, highest I/O, most unstable, largest
  regression, and queries with no benchmark evidence yet.
* `pg_stat_statements` keeps no percentiles, so the p95 shown is **estimated** (mean + 1.645 standard deviations) and
  labelled that way. A measured p95 appears only for queries you benchmark.
* Import twice and the page compares the snapshots: counters are cumulative, so it works out the mean over the calls
  made *between* them and flags queries that got 1.5× slower or more.
* **History** groups every analysis by query shape: how often it was analysed, measured latency, plan changes, and the
  best evidence level reached.
* The sample database comes with demo traffic (a baseline run at first start; **Generate demo traffic** and
  **Simulate a regression** on the page).

![The workload ranking and panels](docs/screenshots/06-workload.png)

## Does a model help choose the index? (honest answer: not yet)

Index ideas carry a separate **Prediction** block (expected speedup, probability of helping, index size, regression
risk, and the top three reasons) from a LightGBM model trained on 3,670 measured candidates. It is advice, never
evidence, and it does not change the evidence badge. On held-out query shapes the model did **not** order suggestions
better than the planner score, so the planner score keeps ordering them; see [docs/ml-evaluation.md](docs/ml-evaluation.md)
for the numbers and limits. The **Models** page shows the comparison, calibration and live monitoring. Rebuild the model
with `python -m querydoctor.lab.dataset_gen` (measures candidates; resumable) and `python -m querydoctor.ranking.train_cli`.

## Explanations, your own databases, privacy

* **Explain** (on every analysis) writes a plain-language explanation of the plan, findings, trade-offs, next experiment
  or a pull-request comment. Each sentence cites numbered facts shown beside it, so every number is traceable. A
  deterministic template always works; with `QD_OLLAMA_MODEL` set (a local Ollama model) a draft is requested and
  **validated**: invented numbers, uncited sentences or SQL are removed or rejected and the template is used instead.
  To use a local model, install [Ollama](https://ollama.com), pull a model (for example `ollama pull llama3.1`), and start
  the stack with `QD_OLLAMA_MODEL=llama3.1` (the API reaches the host's Ollama at `http://host.docker.internal:11434`;
  override with `QD_OLLAMA_URL`). With no model set, or if Ollama is down or slow, the template answers and says why.
* **Your own PostgreSQL databases** can be registered in Settings once an operator sets `QD_ALLOW_CUSTOM_CONNECTIONS=true`
  (off by default). Hosts are checked against loopback, link-local, metadata and private ranges, DNS rebinding is
  closed, and passwords are encrypted and never shown. See [docs/security.md](docs/security.md).
* **Privacy:** the Settings page lists what is stored, queries and parameters never reach the logs, and an analysis or
  connection can be deleted at any time.

## How it works

```mermaid
flowchart LR
    UI["Next.js UI"] --> API["FastAPI"]
    API --> Meta[("metadata\nPostgres")]
    API --> Redis[("Redis")]
    Redis --> Worker["Celery worker"]
    Worker --> Target[("target-postgres\nread-only role, HypoPG")]
    Worker --> Bench[("bench-postgres\nthrowaway clones")]
    Worker --> Mongo[("MongoDB\nread-only user, bench_* sandboxes")]
    Worker --> Meta
```

Each database engine is an **adapter** behind one interface (validate, collect schema, explain, normalize the plan,
diagnose, generate candidates, test, benchmark, collect workload), so the pipeline, the API and the UI are the same for
both.

An analysis moves through increasingly expensive, increasingly trustworthy stages:

1. **Validate** the SQL: a single `SELECT`, no DDL/DML, no dangerous functions.
2. **Plan** with `EXPLAIN (FORMAT JSON)`, or `EXPLAIN ANALYZE` when you chose to execute.
3. **Diagnose** with ten deterministic rules ([catalog](docs/rule-catalog.md)).
4. **Generate** index candidates and prune redundant ones.
5. **Test** each with HypoPG (planner only, nothing created).
6. **Benchmark** the ones you approve in a clone of the database.

See [docs/architecture.md](docs/architecture.md) for the details and [docs/security.md](docs/security.md) for the
threat model.

### Benchmark methodology

For each approved candidate, on a dedicated instance:

1. Clone the sample database (`CREATE DATABASE … TEMPLATE`), recording the PostgreSQL version, relevant settings and a
   dataset snapshot id.
2. Warm up, then time the original query in **5–10 measured runs** (default 7).
3. Create the **real** index, time the build, `ANALYZE`, and note its size.
4. Warm up and time the same query with the same parameters.
5. Compare **medians and 95th percentiles**, the coefficient of variation, and a server-side checksum of the query
   output, so a faster but different answer is never reported as an improvement.
6. Drop the clone, always (a janitor also removes clones abandoned by a crashed worker).

Timings are `EXPLAIN (ANALYZE)` execution times; the instrumentation overhead is equal on both sides. Warnings are
attached when variance is high, runs are too few, the first run dominates (cache warming), or the output differs. A
regression is shown as "slower", never hidden, and only a faster run with matching output earns *Benchmark-validated*.

## The curated slow queries

| Query | What goes wrong | What QueryDoctor finds |
|---|---|---|
| Recent orders for one customer | no index on `customer_id` | large filtered sequential scan → `(customer_id, created_at)` |
| Orders by status in a date window | skewed status + narrow range | sequential scan → `(status, created_at)` |
| Orders joined to payments | unindexed join key | scan → `payments(order_id)` |
| Large result sorted for no reason | sort spills to disk | sort spill |
| `lower(email) = $1` | function defeats an existing index | expression index on `lower(email)` |
| `SELECT *` across four tables | 21 columns returned | wide result |
| Correlated subquery | inner scan repeated per row | repeated loop → `orders(customer_id)` |
| Country + currency filters | correlated columns | 20× cardinality misestimate |

The golden tests assert each of these against real PostgreSQL.

### The MongoDB catalog

| Query | What goes wrong | What QueryDoctor finds |
|---|---|---|
| Orders of one customer | no index on `orders.customer_id` | collection scan → `{customer_id: 1}` |
| A rare status in a narrow date window | the status index narrows, every match is fetched | scan amplification → `{status: 1, created_at: 1}` |
| Sorting a large result in memory | 300k events sorted to return 20 | blocking sort → `{event_type: 1, payload: 1}` |
| An index with the range field first | `created_at` precedes `customer_id` | key amplification and field order → `{customer_id: 1, created_at: 1}` |
| `$lookup` into an unindexed field | every customer scans all orders | large lookup → `{customer_id: 1}` |
| Filtering after grouping | all orders grouped, one group kept | stage order |
| An unbounded `$group` | every event read and grouped | unbounded aggregation |
| An index that matches most documents | 60% of orders are `completed` | unselective index |

The golden tests assert each of these against real MongoDB, and five of them are benchmarked and validated.

## Development

```bash
python -m venv .venv && .venv/Scripts/activate        # use .venv/bin/activate on macOS/Linux
pip install -e "packages/querydoctor[dev]" -e "apps/api[dev]"
docker compose -f infrastructure/compose.yaml up -d --build   # the integration tests need the stack

python -m pytest                          # backend: unit + live-database integration + golden tests
python -m pytest -m chaos apps/api/tests/chaos   # failure injection against the running stack
cd apps/web && npm ci && npm test         # frontend unit tests (Vitest)
npm run e2e                               # every user journey, PostgreSQL and MongoDB, in Chrome (Playwright)
```

Integration tests skip themselves with a clear message when the Docker lab is not running.

| Layer | Tests |
|---|---|
| Plan parser, ten diagnosis rules, candidates, statistics, reports | pytest on committed plan fixtures (no database needed) |
| SQL safety, guarded sessions, HypoPG, sandbox, benchmark runner | pytest against the live lab |
| API: pipeline, SSE, benchmarks, adversarial input | pytest (in-process) and against the running stack |
| Golden tests | every catalog query diagnosed and its index validated on real PostgreSQL |
| Workload | statement merging, ranking, regression and plan-change detection (pure); import and ranking against the live lab, for both engines |
| MongoDB | validator (hostile specs), plan normalization on live-captured explain fixtures, rules, ESR candidates, sandbox, benchmark and golden tests against real MongoDB |
| Ranking model | features, grouped splits, metrics, calibration, the promotion guard (pure); a fresh-process model load |
| Explanations, network guard, privacy | claim validator, template, Ollama fallbacks; address classification and DNS pinning; log redaction |
| Failure injection (`-m chaos`) | lock contention, a restarted database, a killed worker, a hanging model |
| UI components and logic | Vitest + Testing Library |
| The user journey | Playwright |

## Layout

```
packages/querydoctor/   the engine: safety, plan_parser, diagnosis, candidates, benchmark, workload, db, lab,
                        adapters (postgres, mongodb), mongo, ranking, explain, netguard
apps/api/               FastAPI control plane + Celery worker + Alembic migrations
apps/web/               Next.js UI (Monaco, React Flow, Recharts)
infrastructure/         Docker images and compose.yaml
fixtures/               committed plan fixtures and the slow-query catalog
docs/                   architecture, security, rule catalog, design spec and implementation plan
```

## Scope of this release

PostgreSQL 16 `SELECT` queries and MongoDB 7 `find` / `aggregate` reads, one at a time, against the sample databases.
Your own PostgreSQL databases are available behind an off-by-default flag; MongoDB is sample-only. Not included: authentication
and sharded or replica-set MongoDB topologies. MongoDB roles cannot be scoped to database names, so the benchmark user may
create and drop any database on its server: use a dedicated MongoDB for benchmarks anywhere that matters
([docs/security.md](docs/security.md#mongodb)). The design is in [docs/superpowers/specs](docs/superpowers/specs) and the build
plans in [docs/superpowers/plans](docs/superpowers/plans).

The SQL editor (Monaco) loads from a CDN; without network access the page falls back to a plain text area.
