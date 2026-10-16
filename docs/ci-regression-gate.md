# CI regression gate

Set `QD_CI_DSN` to a dedicated ephemeral PostgreSQL 16 instance, using a non-superuser CREATEDB role.
Install HypoPG in template1 first. The GitHub workflow provisions this environment; no production DSN is needed.

```bash
python scripts/ci_check.py --base-ref origin/main --head-ref HEAD --config .querydoctor/ci.json
```

The PR workflow compares exact Git base/head commits. `.querydoctor/ci.json` declares coverage:

```json
{
  "sql_directories": ["fixtures/ci/baseline", "fixtures/ci/candidate"],
  "workload": "fixtures/ci/workload.json"
}
```

Each revision becomes a complete independent schema snapshot. Directory order is significant; filenames sort
lexically within each directory. Put deterministic seeds after migrations. Workload entries accept `id`, `sql`
or `sql_file`, `params`, and `file`. Query IDs and files are paired across revisions. New queries are replayed
against both schemas when possible. Parameters must remain equal to produce comparable results.

Changed SQL, Prisma schemas, migration files and query files outside configured coverage produce
`INSUFFICIENT_EVIDENCE`, including the uncovered paths. They cannot silently pass via unrelated demo inputs.
The checked-in profile covers the sample orders schema, not QueryDoctor's separate API metadata schema.

## Alembic / Prisma exporters

A `schema_export` command can produce complete PostgreSQL schema SQL on stdout. The runner executes it separately
inside a temporary snapshot of each exact Git revision, with a 120-second timeout. Commands are argument lists,
never shell strings. Install the application's pinned migration dependencies in CI before invoking the gate.

For example, an Alembic profile (supply seed/workload files for that schema):

```json
{
  "schema_export": {
    "command": ["python", "-m", "alembic", "upgrade", "head", "--sql"],
    "working_directory": "apps/api",
    "covers": ["apps/api/alembic"]
  },
  "sql_directories": ["fixtures/metadata-seed"],
  "workload": "fixtures/metadata-workload.json"
}
```

`covers` declares the files/directories exercised by the exporter. Alembic migrations must support
[offline SQL generation](https://alembic.sqlalchemy.org/en/latest/offline.html); live-data-dependent Python logic
requires separately tested SQL snapshots.

For Prisma, its pinned CLI can run `migrate diff --from-empty --to-schema prisma/schema.prisma --script` as the
command, with schema/migration paths in `covers`. See the
[Prisma CLI reference](https://docs.prisma.io/docs/orm/reference/prisma-cli-reference).
Prefer committed `migration.sql` histories through `sql_directories` when they contain data transforms or SQL
outside Prisma's schema model. Raw SQL and Flyway SQL migration histories work through `sql_directories` directly.

## Explicit inputs and evidence

The directory interface remains available:

```bash
python scripts/ci_check.py --baseline schema-and-seed/ --candidate incremental-migrations/ --workload workload.json
```

Use `--candidate-mode full` for an independent full candidate schema. A no-op SQL migration is valid.
Workloads contain 1–50 bounded SELECT queries with `$1` parameters. Avoid volatile functions and nondeterministic seeds.

The comparison warms both databases and alternates seven measured executions, reporting medians, p95 and CV.
Row counts plus count-prefixed SHA-256 digests verify output content. Ordering contracts between rewritten queries
need separate validation: row-order changes alone do not imply a content mismatch. Reads have timeouts and run
read-only; migrations execute only in disposable databases.

Outcomes: `OPTIMIZED`, `PASS`, `REGRESSION_DETECTED`, `INSUFFICIENT_EVIDENCE`. Decision order: a content difference is a regression; fewer than 5 runs or no baseline signal is insufficient; a change of 10% or more
is a regression only if the new median exceeds the old p95, and an improvement only if the new p95 is below the old median (separated distributions beat a high CV);
For overlapping distributions, CV above 25% prevents a pass even when all runs take less than 1 ms. Missing evidence or a failed migration/export also prevents a pass.
Content differences are regressions regardless of speed. Exit codes: 0 pass/optimized, 1 regression, 2 insufficient evidence.

Optimization reuses diagnosis, HypoPG and the production isolated benchmark runner for up to three index candidates.
Only the existing verification gate awards Benchmark-Validated. Optional optimization errors never erase measured
migration regressions. The comment distinguishes migration latency from each proposed index experiment.

Artifacts include Markdown, complete JSON evidence, raw SQL, Prisma `migration.sql`, and Alembic Python files.
Review generated Alembic `down_revision` and rollback before applying: another application's migration head is not
known to the exporter. Nothing is applied to a source database.

Fork PRs retain artifacts but cannot comment with a read-only token. The workflow does not execute PR code through
`pull_request_target`. To demonstrate a regression, add `DROP INDEX orders_customer_created;` to the configured
candidate migration history on a branch. Run on a quiet host; noisy evidence deliberately fails closed.

The hosted workflow uses Ubuntu 24.04 and the maintained Node 24 action versions documented by
[checkout](https://github.com/actions/checkout), [setup-python](https://github.com/actions/setup-python),
[upload-artifact](https://github.com/actions/upload-artifact) and [github-script](https://github.com/actions/github-script).
