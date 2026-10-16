# Customer database release: implementation specification

## Goal and release boundary

QueryDoctor runs in a customer controlled deployment. It reads an explicitly authorized PostgreSQL or MongoDB source, then measures proposed indexes on a separately registered staging database or customer authorized snapshot. The source credential has read privileges only. QueryDoctor never creates or drops an index on a source. A report is a reviewable proposal; production application is a separate human approved operation.

The first three pilots are two PostgreSQL workloads and one MongoDB workload. Each must complete connection, workload capture, analysis, isolated benchmark, and decision pack in one session without developer intervention. A defensible decision to make no change counts as a useful outcome.

## Current behavior and required changes

| Area | Present | First release |
| --- | --- | --- |
| Engine contract | `DatabaseAdapter` exposes validation, schema, explain, diagnosis, `generate_candidates`, `test_candidates`, benchmark, and workload collection for PostgreSQL and MongoDB. | Preserve the adapter boundary; add engine specific capabilities and evidence labels. |
| Sources | Demo connections exist for both engines. Opt in custom PostgreSQL records are encrypted, destination checked at registration and use, and run in read only sessions. | Authenticate users; authorize every project and resource request; make connection records engine aware; permit bounded custom MongoDB find and aggregate analysis; never use a configured demo URI when a connection is selected. |
| Workload | PostgreSQL imports cumulative `pg_stat_statements`; MongoDB reads at most 5,000 recent `system.profile` records. Snapshots can be compared by engine. | Select a project source; create immutable snapshots with source connection, capture time window and method, coverage and truncation, engine version, schema and index fingerprints, and ranked shapes. Import MongoDB profiler samples first. Add an Atlas collector only after capability and permission checks, with a versioned parser and a second capture path. Never enable profiling silently. |
| Candidate evidence | PostgreSQL uses HypoPG to test hypothetical indexes. MongoDB derives ESR candidates without a hypothetical index facility. | Evaluate bounded candidates across relevant shapes in the same snapshot, including MongoDB ESR and ERS alternatives when justified; record benefit, affected and potentially harmed shapes, index size, write cost, overlap, and confidence. Keep the learned ranker separate from validation until held out evaluation supports it. |
| Benchmark | Demo PostgreSQL clones and MongoDB `bench_*` databases are measured. Custom PostgreSQL benchmarks are blocked. MongoDB demo sandbox uses the benchmark deployment. | Register a distinct benchmark target and credential. Preflight version, schema, indexes, data scale, disk, and privileges; reject incompatible or same deployment targets. Apply indexes only there, with size limits, cancellation, and crash cleanup. Keep custom source benchmarking blocked until preflight and isolated execution are verified. |
| Report | Markdown and HTML analysis reports include current findings and benchmark results. | Export a change proposal with command, rollback, workload impact, snapshot and target identity, method, distributions, output check, uncertainty, warnings, and approval history. Add a CI comparison against a saved baseline with project policy thresholds and outcomes `pass`, `measured_regression`, or `insufficient_evidence`. |
| Deployment | Docker Compose is a local demo. No authentication exists. | Ship a clean self hosted install, sample data, setup and permission guides, backup and retention instructions, and a troubleshooting path. Qualify standalone, replica set, and sharded MongoDB separately; label supported topologies. |

## Security and ownership

The operator provisions identities. Project membership grants explicit read, analyst, and approver rights. Every route that takes a project ID, connection ID, analysis ID, snapshot ID, candidate ID, benchmark ID, or report ID resolves its owning project and checks membership before returning data or starting work. Unknown and unauthorized IDs return the same 404 response. A URL identifier never grants access. Worker jobs use the already authorized, stored project and connection IDs and recheck destination DNS at connect time; the checked IP is pinned while preserving the host name for TLS. Credentials remain encrypted at rest, excluded from responses, reports, and logs, and wiped on deletion. Query parameters are retained only in protected query snapshots for repeatability; reports show parameter set identifiers and nonsecret type/shape metadata, not values.

Custom MongoDB credentials should have `find` and required metadata privileges only. The connection must pin the checked address and authenticate against the chosen database or a separately supplied auth database. The API must never accept an arbitrary URI with embedded options. The benchmark credential belongs to the isolated target and is distinct from the source credential.

## Evidence and claim record

Each claim stores: engine and exact server version; normalized query shape/fingerprint; parameter set identifier and protected bound values; immutable source snapshot ID, capture window, method and coverage; schema and existing index fingerprints; candidate index state and command; benchmark target ID and environment differences; run count and raw before/after timing samples or a lossless distribution; median, observed p95 and variability; index build time and measured size; output equivalence method and result; observed index use; warnings, failures and approval history. A query shape can contribute positive, negative, or insufficient evidence. Repeatability requires a saved fixture/snapshot reference, configuration, server version, and target preparation steps.

Evidence labels are strict: `suggested` means shape/rule support only; `planner_validated` applies to PostgreSQL HypoPG when the planner used the hypothetical index with a meaningful plan improvement; `benchmark_validated` requires an isolated, repeated measurement with matching output, observed index use, at least 10% median improvement, and improved p95 under the existing separation and warning gate. MongoDB has no HypoPG equivalent and cannot receive the PostgreSQL planner label. A failed or incomplete benchmark leaves the idea suggested or planner validated as applicable. MongoDB `explain()` bypasses the plan cache, so explain results are plan evidence, never workload latency samples.

PostgreSQL `pg_stat_statements` supplies aggregate count, mean and standard deviation, not measured p95. `mean + 1.645 × stddev` must always be labeled **estimated p95**; benchmark p95 is calculated from observed runs. A MongoDB profiler import is a bounded sample, not complete history; record capture window, count, limit, sampling/profiling settings if known, and coverage `unknown` when they cannot be established. Whole millisecond profiler times retain that precision. A low MongoDB `$indexStats` count alone cannot justify a drop because counters reset after events such as restart or index recreation.

## Delivery order and gates

1. Add identity, project membership and resource authorization, then engine aware source records and custom MongoDB analysis. Prove cross project non-disclosure, credential redaction, DNS rebinding defense, and custom source routing.
2. Add immutable source selected workload snapshots and imported MongoDB profiler samples. Prove comparison, ranking reasons, coverage display, and observed versus estimated percentile labels.
3. Evaluate bounded candidates across snapshot shapes and tradeoffs. Prove the report distinguishes shape, planner, and benchmark support and records harmed queries.
4. Add explicit benchmark targets, preflight and isolated execution. Prove the source is never modified, before/after median and p95, variability, build cost, output equivalence, environment differences, cancellation, and crash cleanup. Retain existing validation thresholds.
5. Export decision packs and policy driven CI comparisons; run backend, frontend, integration, golden, chaos, and browser suites for both engines. Test clean machine installation and three guided pilots before publishing performance claims.

The customer benchmark boundary is the main uncertainty. No custom source can be called benchmark validated until that target mode passes its preflight and cleanup tests.
