# Implementation audit

Reviewed 16 October 2026 against the four-phase product brief. This records what is implemented and the practical limits of the evidence; it does not equate suggested indexes with measured improvements.

| Requirement | Implementation and evidence |
|---|---|
| Four instant showcase cards | `apps/web/components/OfflineAnalyzer.tsx`, `lib/showcaseFixtures.ts`, and `lib/showcaseCaptured.json`. Real PostgreSQL/MongoDB pipeline captures include normalized/raw plans, HypoPG comparisons where supported, seven latency samples per condition, row counts, actual SHA-256 digests, and after plans. The false positive passes HypoPG but fails the benchmark gate. |
| Paste PostgreSQL JSON/text and MongoDB executionStats | `apps/web/lib/pasteExplain.ts`. Browser-only parsing, highlighted bottleneck, deterministic unverified suggestions; bounded input/tree size and explicit rejection of multiple plans. No connection, API, HypoPG, or measurement is implied. |
| Vercel without databases | Demo mode defaults on when `VERCEL=1`; live routes/actions are hidden or redirected. The Workload page uses a real recorded batch capture. Local full mode retains the API and live runner. |
| Git-aware CLI regression gate | `scripts/ci_check.py`, `packages/querydoctor/src/querydoctor/ci/`. Exact PR base/head schema snapshots and stable-ID workloads, deterministic seeded databases, alternating measured runs, output checks, HypoPG and isolated index experiments. Uncovered changed database files fail closed. |
| Alembic/Prisma/Flyway/raw SQL | Ordered SQL migrations work directly, including Flyway SQL naming. Configurable offline schema exporters support native tools such as Alembic and Prisma; an Alembic offline export was exercised locally. Arbitrary Python/data migrations are not automatically translated or guessed. |
| PR report and migration exports | Markdown/JSON reports, median and variability, checksum verdicts, index DDL, collapsible snippets, and raw SQL/Alembic/Prisma artifacts. The Actions workflow updates a bot-marked PR comment and uploads evidence. Fork PRs receive artifacts without privileged comment writes. |
| Ranked workload fixes | Project/source/engine-scoped evidence, top-N analysis, separately bounded Celery jobs, explicit isolated benchmark approval, identical-index consolidation and per-query deduplication. Only qualifying measured fixes contribute savings. |
| Time saved per day | Requires a known statistics capture window; otherwise reports savings within the window. Values estimate query execution time, not CPU time. They extrapolate sample bindings and isolated fixes; combined-index interactions are not measured. |
| README and one-command demo | Hook, real benchmark table, real 60-second `docs/demo.gif`, verification ladder, CI architecture, and measured planner/ML findings. `run_demo.sh` builds, seeds, checks readiness and prints the three-click path. Demo/full volumes are separate and retained. |

## Verification

- Full Python suite: 1,309 passed, one parser-compatibility case skipped; zero failures/errors (1,310 collected, 421.9 seconds). PostgreSQL, MongoDB, API/worker and isolated sandbox integration tests were included with the rebuilt local stack required. Fourteen focused CI tests also pass after removing a permissive sub-millisecond noise exception.
- Frontend: 216 unit tests pass in 29 files. TypeScript production builds pass in both full local and Vercel showcase modes; changed-file ESLint and two offline production-browser checks pass.
- The Vercel-mode browser test blocks all `/api/` traffic, exercises the four-card/paste navigation and recorded Workload queue, and verifies live-only routes cannot mount the backend UI.
- Offline showcase selection measured 78.8–84.0 ms through two animation frames on this machine. This is a local interaction measurement, not a guarantee for initial page download or every browser/device.
- A temporary Git repository with a real dropped-index candidate produced `REGRESSION_DETECTED` and matching output checksums against disposable PostgreSQL 16/HypoPG. The generated SQL, Prisma and Alembic artifacts were checked.
- Recorded showcase payloads are checked against the same backend verification gate. SHA-256 checks retain duplicates, are order independent, and include row/document counts. A disabled MongoDB internal-function capability fails the benchmark instead of granting validation.
- Git diff whitespace and publishable-source secret checks passed. Test artifacts, environments and credentials are excluded from Git.
- The rebuilt local batch flow completed three analyses and four independently queued isolated benchmarks, all with matching SHA-256 output checks. The worker registers separate batch/item/benchmark tasks.

## Remaining configuration for another application

The supplied `.querydoctor/ci.json` covers the seeded sample schema. For a real application, provide its baseline/migration path (or offline export command), representative sanitized seed data, query SQL and stable bound parameters. The gate refuses to claim coverage for unknown changed migration/query files. Native migration exports must run deterministically without production access.

Automatic batch benchmarks currently replay PostgreSQL sample-catalog queries. Customer `pg_stat_statements` does not contain representative bind values or an approved isolated data clone; those require an explicitly seeded CI workload. The product skips unsupported shapes rather than guessing parameters or copying customer data.

Generated Alembic files need the application's real `down_revision`; rollback policy also requires review. A custom domain and a hosted live API/database are optional and are not needed for the zero-setup showcase. Full-scale volume configuration is checked; cold-build duration and a full-scale seed have no two-minute guarantee.

## Publication

Source is published to [mrhpsingh01/query_doctor](https://github.com/mrhpsingh01/query_doctor). The deliberately unmergeable [draft regression demonstration, PR #1](https://github.com/mrhpsingh01/query_doctor/pull/1), ran the workflow on GitHub's hosted runner: 0.094 ms → 9.502 ms, matching output, `REGRESSION_DETECTED`. The bot posted its report with a measured index experiment (9.369 ms → 0.053 ms), and the enforcement step failed as intended. SQL/Alembic/Prisma evidence artifacts were uploaded. The demo must not be merged.

Target Vercel team: `mrhpsingh01s-projects`, new frontend project `query-doctor`, root directory `apps/web`. The repository-only Vercel GitHub app installation was explicitly approved; the deployment URL will be recorded here after the first build completes.
