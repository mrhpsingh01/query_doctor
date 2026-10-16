from __future__ import annotations

import argparse
import html
import json
import os
import tempfile
from pathlib import Path

import psycopg
from querydoctor.adapters.postgres import PostgresAdapter
from querydoctor.ci.prepare import prepare_git_inputs, CoverageError
from querydoctor.ci.migrations import export_migrations
from querydoctor.benchmark.checksum import result_checksum
from querydoctor.benchmark.models import BenchmarkConfig
from querydoctor.benchmark.reliability import awards_validation
from querydoctor.benchmark.runner import run_benchmark
from querydoctor.benchmark.sandbox import Sandbox
from querydoctor.benchmark.stats import summarize
from querydoctor.db import explain, guarded_connection
from querydoctor.diagnosis import Thresholds
from querydoctor.plan_parser import iter_nodes, parse_explain


def classify(before, after, identical: bool) -> str:
    """Different output is always a regression. Otherwise a change counts only when it is *separated*: the new typical run
    lies beyond the old slow end (regression) or the new slow end beats the old typical run (improvement). Run-to-run
    variability (CV) vetoes only the ambiguous remainder, so a noisy sub-millisecond query that became 70x slower is still
    a regression, while overlapping noise stays inconclusive."""
    if not identical:
        return "REGRESSION_DETECTED"
    if before.n < 5 or after.n < 5 or before.median_ms <= 0:
        return "INSUFFICIENT_EVIDENCE"
    change = (after.median_ms / before.median_ms - 1) * 100
    if change >= 10 and after.median_ms > before.p95_ms:
        return "REGRESSION_DETECTED"
    if change <= -10 and after.p95_ms < before.median_ms:
        return "OPTIMIZED"
    if max(before.cv, after.cv) > 0.25:
        return "INSUFFICIENT_EVIDENCE"
    return "PASS" if abs(change) < 10 else "INSUFFICIENT_EVIDENCE"


def apply_sql(dsn: str, directory: Path) -> None:
    if not directory.is_dir():
        raise ValueError(f"SQL directory does not exist: {directory}")
    files = sorted(directory.rglob("*.sql"))
    if not files:
        raise ValueError(f"No SQL migrations found in {directory}; export Alembic/Prisma migrations to SQL first.")
    with psycopg.connect(dsn, autocommit=True, options="-c statement_timeout=60000 -c lock_timeout=3000") as conn:
        for file in files:
            # These are explicitly supplied migration scripts, executed only in new disposable databases.
            conn.execute(file.read_text(encoding="utf-8"))
        conn.execute("ANALYZE")


def measure_pair(base_dsn, candidate_dsn, query, runs=7):
    adapter = PostgresAdapter()
    params = query.get("params", [])
    texts = [query["sql"], query.get("candidate_sql", query["sql"])]
    for text in texts:
        adapter.validate_query(text, params, max_chars=20000)
    timings = [[], []]
    plans = [None, None]
    checksums = [None, None]
    row_counts = [None, None]
    with guarded_connection(base_dsn, role_kind="bench_admin", statement_timeout_ms=15000) as before, guarded_connection(candidate_dsn, role_kind="bench_admin", statement_timeout_ms=15000) as after:
        connections = [before, after]
        for conn, text in zip(connections, texts):
            conn.read_only = True
            explain(conn, text, params, execute=True)  # warm each side
        # Alternate order to reduce drift bias, with identical instrumentation and bound parameters.
        for run in range(runs):
            for side in ([0, 1] if run % 2 == 0 else [1, 0]):
                raw = explain(connections[side], texts[side], params, execute=True)
                timings[side].append(raw[0]["Execution Time"])
                plans[side] = parse_explain(raw)
                row_counts[side] = raw[0]["Plan"].get("Actual Rows")
        for side in range(2):
            checksums[side] = result_checksum(connections[side], texts[side], params, ordered=False)
    stats = [summarize(samples) for samples in timings]
    identical = checksums[0] == checksums[1] and row_counts[0] == row_counts[1]
    return {
        "file": query.get("file", query.get("id", "query")), "id": query.get("id", "query"),
        "outcome": classify(*stats, identical), "before": stats[0].model_dump(), "after": stats[1].model_dump(),
        "samples": timings, "checksums": checksums, "row_counts": row_counts, "checksum_match": identical,
        "plans": [p.model_dump(mode="json") for p in plans], "fixes": [],
        "bottleneck": max(iter_nodes(plans[1].root), key=lambda n: n.exclusive_time_ms or 0).node_type,
    }


def optimize(admin_dsn, candidate, query):
    adapter = PostgresAdapter()
    params = query.get("params", [])
    vq = adapter.validate_query(query.get("candidate_sql", query["sql"]), params, max_chars=20000)
    with guarded_connection(candidate.dsn, role_kind="bench_admin") as conn:
        schema = adapter.collect_schema(conn, vq)
        plan = adapter.normalize_plan(adapter.explain(conn, vq, params, execute=True))
        findings = adapter.diagnose(plan, schema, vq, Thresholds())
        kept, _ = adapter.generate_candidates(vq, schema, findings)
        baseline = adapter.normalize_plan(adapter.explain(conn, vq, params, execute=False))
        scored = adapter.test_candidates(conn, vq, params, baseline, kept[:3], schema)
    fixes = []
    for score in scored:
        comparison = score.comparison
        planner_valid = bool(comparison and comparison.uses_candidate and (comparison.cost_reduction >= .10 or comparison.operator_improvement >= .5))
        fix = {"ddl": score.candidate.ddl, "validation": "Planner-Validated" if planner_valid else "Suggested", "comparison": comparison.model_dump() if comparison else None, "error": score.error}
        if planner_valid:
            try:
                result = run_benchmark(admin_dsn, vq.sql, params, score.candidate.ddl, BenchmarkConfig(), template=candidate.name)
                fix["benchmark"] = result.model_dump(mode="json")
                if awards_validation(result):
                    fix["validation"] = "Benchmark-Validated"
            except Exception as exc:
                fix["error"] = type(exc).__name__  # never expose connection strings
        fixes.append(fix)
    return fixes


def cell(value):
    return html.escape(str(value)).replace("|", "&#124;").replace("\n", " ").replace("`", "&#96;")


def render_report(rows):
    failed = any(r["outcome"] == "REGRESSION_DETECTED" for r in rows)
    uncertain = not rows or any(r["outcome"] == "INSUFFICIENT_EVIDENCE" for r in rows)
    heading = "⚠️ Performance Regression / Unindexed Scan Detected" if failed else "⚠️ Insufficient evidence" if uncertain else "✅ Pass"
    lines = ["<!-- querydoctor-pr-report -->", f"# QueryDoctor: {heading}", "", "Median and CV from alternating isolated runs. HypoPG cost is not measured latency.", "", "| File / Query | Bottleneck | Verification level (best fix) | Baseline → Candidate median (CV) | Result checksum | Outcome |", "|---|---|---|---|---|---|"]
    for row in rows:
        before, after = row.get("before"), row.get("after")
        latency = f"{before['median_ms']:.3f} ms ({before['cv']:.1%}) → {after['median_ms']:.3f} ms ({after['cv']:.1%})" if before else "Unavailable"
        levels = [f["validation"] for f in row.get("fixes", [])]
        level = next((v for v in ("Benchmark-Validated", "Planner-Validated", "Suggested") if v in levels), "Suggested")
        checksum = "✅ Identical (rows + digest)" if row.get("checksum_match") else "❌ Different" if before else "Not verified"
        lines.append("| " + " | ".join(map(cell, [row["file"], row.get("bottleneck", "Unavailable"), level, latency, checksum, row["outcome"]])) + " |")
        if row.get("coverage_detail"):
            lines.extend(["", "Coverage incomplete: " + cell(row["coverage_detail"]), ""])
        for fix in row.get("fixes", []):
            ddl = fix["ddl"].replace("CREATE INDEX ON", "CREATE INDEX CONCURRENTLY ON", 1).rstrip(";") + ";"
            # SQL comes only from the validated candidate generator, never arbitrary report text.
            lines.extend(["", f"<details><summary>Index idea — {cell(fix['validation'])}</summary>", ""])
            comparison = fix.get("comparison")
            if comparison:
                lines.extend([f"HypoPG estimated cost: {comparison['cost_before']:.2f} → {comparison['cost_after']:.2f}.", ""])
            benchmark = fix.get("benchmark")
            if benchmark:
                original, indexed = benchmark["before"], benchmark["after"]
                checksum = "✅ Identical" if benchmark["checksum_match"] else "❌ Different"
                lines.extend([
                    "| Isolated index experiment | Before | With index |", "|---|---|---|",
                    f"| Median | {original['median_ms']:.3f} ms | {indexed['median_ms']:.3f} ms |",
                    f"| p95 | {original['p95_ms']:.3f} ms | {indexed['p95_ms']:.3f} ms |",
                    f"| CV | {original['cv']:.1%} | {indexed['cv']:.1%} |",
                    f"| Runs | {original['n']} | {indexed['n']} |", "",
                    f"Result checksum: {checksum}. Candidate index used: {benchmark['index_used']}.", "",
                ])
                lines.extend(f"- {cell(warning)}" for warning in benchmark.get("warnings", []))
                lines.append("")
            if fix.get("error"):
                lines.extend([f"Validation incomplete: {cell(fix['error'])}.", ""])
            lines.extend(["```sql", ddl, "```", "", "Raw SQL / Prisma migration.sql: use the SQL above outside a transaction.", "", "Alembic (PostgreSQL concurrent index):", "```python", "with op.get_context().autocommit_block():", f"    op.execute({ddl!r})", "```", "", "Review table size, locks and write overhead before applying. Nothing is applied to the source database.", "</details>"])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, help="Ordered baseline SQL directory, including deterministic seed SQL")
    parser.add_argument("--candidate", type=Path, help="Incremental candidate SQL migration directory")
    parser.add_argument("--workload", type=Path)
    parser.add_argument("--output", type=Path, default=Path("querydoctor-report.md"))
    parser.add_argument("--base-ref", help="Git base commit; compare configured SQL snapshots across revisions")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--config", default=".querydoctor/ci.json")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--candidate-mode", choices=["incremental", "full"], default="incremental")
    args = parser.parse_args(argv)
    rows = []
    temporary = tempfile.TemporaryDirectory(prefix="querydoctor-ci-")
    provenance = None
    try:
        if args.base_ref:
            args.baseline, args.candidate, args.workload, provenance = prepare_git_inputs(args.repository, args.base_ref, args.head_ref, args.config, Path(temporary.name) / "inputs")
            args.candidate_mode = "full"
        if not all((args.baseline, args.candidate, args.workload)):
            raise CoverageError("Supply --base-ref with config, or --baseline, --candidate and --workload")
        dsn = os.environ["QD_CI_DSN"]  # dedicated non-superuser CREATEDB role; template1 has HypoPG
        workload = json.loads(args.workload.read_text(encoding="utf-8"))
        if not isinstance(workload, list) or not 1 <= len(workload) <= 50:
            raise ValueError("workload.json must contain 1–50 query objects")
        with Sandbox(dsn, template="template1") as base:
            apply_sql(base.dsn, args.baseline)
            with Sandbox(dsn, template="template1" if args.candidate_mode == "full" else base.name) as candidate:
                apply_sql(candidate.dsn, args.candidate)
                for query in workload:
                    try:
                        row = measure_pair(base.dsn, candidate.dsn, query)
                        try:
                            row["fixes"] = optimize(dsn, candidate, query)
                        except Exception as exc:
                            # An optional optimization failure must never erase a measured regression.
                            row["optimization_error"] = type(exc).__name__
                    except Exception as exc:
                        row = {"file": query.get("file", query.get("id", "query")), "outcome": "INSUFFICIENT_EVIDENCE", "error": type(exc).__name__}
                    rows.append(row)
    except Exception as exc:
        rows.append({"file": "Setup / migration", "outcome": "INSUFFICIENT_EVIDENCE", "error": type(exc).__name__,
                     "coverage_detail": str(exc) if isinstance(exc, CoverageError) else None})
    finally:
        temporary.cleanup()
    if provenance:
        for row in rows:
            row["git_provenance"] = provenance
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_report(rows), encoding="utf-8")
    args.output.with_suffix(".json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    for i, row in enumerate(rows):
        for j, fix in enumerate(row.get("fixes", [])):
            export_migrations(args.output.parent, f"querydoctor-fix-{i}-{j}", fix["ddl"])
    print(f"QueryDoctor report: {args.output}")
    return 1 if any(r["outcome"] == "REGRESSION_DETECTED" for r in rows) else 2 if any(r["outcome"] == "INSUFFICIENT_EVIDENCE" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
