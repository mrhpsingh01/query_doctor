# Security

QueryDoctor runs SQL that people paste into a text box, so safety is built in front of every database call, not added
after. This release only connects to its own sample databases; that boundary is the main defence and the rest are
layers behind it.

## Threats and mitigations

| Threat | Mitigation | Verified by |
|---|---|---|
| A second statement hidden after a comment, string or `;` | SQLGlot parses the whole text; exactly one statement is allowed. Semicolons inside string literals and `$$` quotes are correctly not separators. | `test_validator`, `test_adversarial` |
| DDL, DML, `COPY`, `SELECT … INTO`, `FOR UPDATE`, data-modifying CTEs | The root must be a query and no write node may appear anywhere in the tree. | `test_validator`, `test_adversarial` |
| Dangerous functions (`pg_sleep`, `pg_read_file`, `dblink*`, `set_config`, `lo_*`, advisory locks, `pg_terminate_backend`, sequence writes, …) | Denylist on exact names and prefixes, including schema-qualified calls and functions in `FROM`. | `test_validator`, `test_adversarial` |
| Parser differences between QueryDoctor and PostgreSQL | (1) User queries run through the extended protocol, which PostgreSQL itself limits to a single statement. (2) The canonical SQL printed from the parsed tree is what runs, and a query is refused unless that text parses back to the identical tree. (3) The session role is read-only and non-superuser whatever the text says. | `test_validator`, `test_explain_integration`, `test_runner_integration` |
| SQL injection through parameters | Only finite scalars are accepted (no containers, NaN/Infinity or NUL). Values are bound as protocol parameters; they are never quoted or placed into the SQL text. | `test_params`, `test_adversarial` |
| A benchmark's measured query writing in the sandbox | Measurement queries run in read-only transactions; only the approved `CREATE INDEX` and `ANALYZE` steps may write. | `test_runner_integration` |
| The stack reachable from other machines | Every published port binds to `127.0.0.1` by default. | `compose.yaml` |
| A query that writes or locks | Explore sessions are read-only (`default_transaction_read_only`), have `statement_timeout=10s`, `lock_timeout=1s`, `idle_in_transaction_session_timeout=15s`, are always rolled back, and refuse superuser roles. | `test_explain_integration` |
| A runaway query | The 10 s limit cancels it; the analysis ends `failed` with a readable reason; the stack keeps serving. | `test_stack` (live) |
| `EXPLAIN ANALYZE` running a query the user did not mean to run | Plain `EXPLAIN` is the default. Execution needs an explicit choice, shown with a warning, and writes an `execute_explain_requested` audit event. | `test_analyses_api` |
| Benchmarks touching the analyzed database | They run in a clone on a separate instance, as a role that exists only there. The template is never connected to. | `test_sandbox_integration` |
| Arbitrary DDL during a benchmark | Only a plain single `CREATE INDEX ON <table> (…)` is ever run; semicolons and comments are rejected. | `test_runner_integration` |
| Leaked sandboxes, or jobs left "running" by a crashed worker | The drop and slot release run first in a `finally`, before any progress reporting that could fail. A janitor (at worker start and before each benchmark) removes clones older than 15 minutes and fails jobs that never finished. Celery tasks have soft/hard time limits. | `test_sandbox_integration`, `test_runner_integration`, `test_janitor`, `test_stale_jobs` |
| Resource exhaustion | Query text capped at 20,000 characters; plans capped at 5 MB; at most two concurrent benchmarks (advisory locks); per-benchmark time limit. | `test_adversarial`, `test_runner_integration` |
| Double approval of a benchmark | Partial unique index: one active run per candidate. | `test_adversarial` |
| SSRF through "connect your own database" | Connections are not supported yet. `/api/connections/test` accepts only an allowlist of the sample hosts and ports; everything else is a 400. | `test_analyses_api` |
| Stored credentials | Connection passwords are Fernet-encrypted at rest. No endpoint returns them. | `test_crypto` |
| Secrets in reports or responses | Reports and API payloads are built from analysis data only; tests assert no connection string or role name appears. | `test_report`, `test_adversarial` |
| XSS through query text or finding titles | The HTML report escapes every value and loads no external assets; React escapes UI output. | `test_report` |
| A client trusting a stale or dishonest label | The evidence level comes from the server and can only be raised by a measured, output-checked benchmark. | `test_benchmarks_api` |

## Custom connections and the network guard

Connections to your own PostgreSQL databases exist only when the operator sets `QD_ALLOW_CUSTOM_CONNECTIONS=true`
(default off: the API answers 403). Passwords are encrypted with Fernet, are never returned or logged, and are overwritten
when a connection is deleted. Every host is checked by `querydoctor.netguard` when it is registered **and again each time
it is used**; the host is resolved once and the connection is made to the checked IP address (`hostaddr`) while the name
is kept for TLS, so a DNS answer that changes later cannot redirect it (rebinding).

| Target | Result |
|---|---|
| `127.0.0.0/8`, `::1`, `localhost`, `*.localhost`, `0.0.0.0` | always blocked |
| `169.254.0.0/16` (including `169.254.169.254`), `fe80::/10` | always blocked |
| Multicast, reserved, documentation ranges, `fec0::/10` | always blocked |
| `10/8`, `172.16/12`, `192.168/16`, `100.64/10`, `fc00::/7` | blocked unless `QD_ALLOW_PRIVATE_NETWORKS=true` |
| IPv4-mapped / 6to4 / Teredo / NAT64 forms of any of the above | classified by the embedded IPv4 address |
| Decimal, hex and octal spellings (`2130706433`, `0x7f000001`, `0177.0.0.1`, `127.1`) | parsed as the IP they denote, then classified |
| A name resolving to several addresses | blocked if **any** of them is blocked |
| Ports other than 5432 (configurable allowlist) | blocked |
| `target-postgres`, `bench-postgres` | allowed by name (the lab's own containers) |

Sessions on custom databases are read-only, run as a non-superuser and time out like the sample's. Benchmarks are
refused for custom connections (409), and index testing is skipped, leaving ideas as *suggested*, when HypoPG is missing.
Connection errors and block reasons never echo credentials or resolver internals.

## Explanations and the language model

Explanations are composed from numbered facts computed from the analysis (never from rows or parameters). A local
Ollama model may draft one, but its draft is checked by the claim validator: every number must match a fact at the
precision shown, sentences must cite facts, and any SQL or DDL rejects the draft; otherwise the deterministic template
is shown with the reason. Nothing an explanation says is executed.

## Logs

A log record factory (`app/logging_utils.py`) removes string and number literals from SQL text, SQLAlchemy parameter
dumps and PostgreSQL error `DETAIL` values from every logger; engines use `hide_parameters=True`.

## Deleting data

`DELETE /api/analyses/{id}` removes an analysis with its findings, ideas, predictions, benchmarks, explanations and
feedback, and the canonical query once nothing else uses it; `DELETE /api/connections/{id}` wipes credentials. `GET
/api/privacy` lists exactly what is stored.

## What is stored

The canonical query and its parameters, the execution plan, schema details (row estimates, column types, indexes),
findings, candidates, benchmark results and an audit trail. **Rows returned by queries are never stored**; the
benchmark compares outputs with a server-side checksum.

Audit events: `analysis_started`, `execute_explain_requested`, `benchmark_approved`, `sandbox_index_created`,
`benchmark_cancelled`, `job_cancelled`, `report_exported`, `connection_created`, `connection_used`, `connection_deleted`,
`explanation_requested`, `explanation_reviewed`, `analysis_deleted`.

## Never automatic

QueryDoctor never applies a change to the analyzed database. Index ideas are text. The only real index it ever
creates lives in a throwaway clone, after you confirm.

## Known limits

* Failure injection (lock contention, a restarted target database, a killed worker, a hanging language model) is covered by
  `pytest -m chaos apps/api/tests/chaos` against the running stack.
* **No authentication.** This release is a single-user demo and must not be exposed to an untrusted network as is.
* The default credentials in `compose.yaml` are for local development. Set `QD_SECRET_KEY` (a Fernet key) and change
  the database passwords for any shared deployment.
* Benchmark isolation is database-level, not process-level. That is appropriate while only QueryDoctor-controlled
  sample data is cloned; connecting customer databases would call for per-run containers or snapshots.
* The denylist is a defence in depth. The real boundary is the read-only, non-superuser role.
