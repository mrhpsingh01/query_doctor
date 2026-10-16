"""Human-readable reports (Markdown and standalone HTML) from an analysis dict.

The input is the plain JSON-able shape the API serves, so rendering has no dependency on the database or ORM. Only values
already in that dict are printed: no connection details and no business rows can appear in a report.
"""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from typing import Any

Block = tuple  # ("h1"|"h2"|"h3"|"p"|"code"|"ul"|"table", ...)

LABELS = {"suggested": "Suggested", "planner_validated": "Planner-validated", "benchmark_validated": "Benchmark-validated"}
MEANING = {
    "suggested": "derived from the query's shape; not tested",
    "planner_validated": "PostgreSQL's planner picks it in a what-if test; not measured",
    "benchmark_validated": "measured faster in an isolated copy of the database",
}


def _ms(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value < 1:
        return f"{value:.2f} ms"
    if value < 1000:
        return f"{round(value)} ms"
    return f"{value / 1000:.1f}".rstrip("0").rstrip(".") + " s"


def _bytes(value: float | None) -> str:
    if value is None:
        return "n/a"
    for unit, size in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if value >= size:
            return f"{value / size:.1f} {unit}"
    return f"{int(value)} B"


def _pct(value: float) -> str:
    return f"{abs(value):.1f}%"


def _kv(evidence: dict[str, Any]) -> list[str]:
    items = []
    for key, value in evidence.items():
        if value is None or isinstance(value, (dict, list)) and not value:
            continue
        if isinstance(value, str):
            # PostgreSQL plan conditions can embed bound values. Keep numeric evidence, omit verbatim conditions.
            continue
        shown = json.dumps(value) if isinstance(value, (dict, list)) else (f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else str(value))
        items.append(f"{key.replace('_', ' ')}: {shown}")
    return items


def _slowest(plan: dict | None) -> str | None:
    if not plan or not plan.get("executed"):
        return None
    best, total = None, plan.get("execution_time_ms")

    def walk(node):
        nonlocal best
        if node.get("exclusive_time_ms") is not None and (best is None or node["exclusive_time_ms"] > best["exclusive_time_ms"]):
            best = node
        for child in node.get("children", []):
            walk(child)

    walk(plan["root"])
    if not best or not total:
        return None
    name = f"{best['node_type']} on {best['relation_name']}" if best.get("relation_name") else best["node_type"]
    return f"About {round(best['exclusive_time_ms'] / total * 100)}% of the time is spent in {name}."


def _plan_change(candidate: dict | None) -> str | None:
    comparison = (candidate or {}).get("comparison") or {}
    removed, added = comparison.get("removed_operators") or [], comparison.get("added_operators") or []
    if not removed and not added:
        return None
    return f"{', '.join(removed) or 'nothing'} → {', '.join(added) or 'nothing'}"


def _blocks(a: dict[str, Any]) -> list[Block]:
    plan = a.get("plan")
    executed = bool(plan and plan.get("executed"))
    b: list[Block] = [("h1", f"QueryDoctor report: analysis {a.get('id')}")]
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    mode = "measured by running the query" if executed else "planner estimates only (the query was not run)"
    b.append(("p", f"Generated {generated}. This analysis was {mode}."))

    b += [("h2", "Query shape"), ("code", a.get("normalized_sql") or "[query shape unavailable]")]
    b.append(("p", f"Parameter set: {len(a.get('params', []))} bound value(s), omitted from this report."))

    b.append(("h2", "Execution"))
    if executed:
        b.append(("ul", [f"Planning time: {_ms(plan.get('planning_time_ms'))}", f"Execution time: {_ms(plan.get('execution_time_ms'))}"]))
        if (line := _slowest(plan)):
            b.append(("p", line))
    else:
        b.append(("p", "These are the planner's estimates only, so there are no real timings. Run the analysis with execution to measure them."))

    b.append(("h2", "Findings"))
    findings = a.get("findings") or []
    if not findings:
        b.append(("p", "No problems were found in this plan."))
    for f in findings:
        b += [("h3", f"{f['title']} ({f['severity']})"), ("p", f["summary"])]
        if (items := _kv(f.get("evidence") or {})):
            b.append(("ul", items))
        if f.get("next_actions"):
            b.append(("p", "What might help: " + " ".join(f["next_actions"])))

    candidates = a.get("candidates") or []
    kept = sorted((c for c in candidates if not c.get("dropped")), key=lambda c: c.get("rank", 0))
    b.append(("h2", "Index ideas"))
    if not kept:
        b.append(("p", "No index ideas for this query."))
    for c in kept:
        b += [("h3", c["ddl"]), ("p", f"Evidence: {LABELS[c['validation']]} ({MEANING[c['validation']]})")]
        facts = []
        if c.get("planner_score"):
            facts.append(f"Planner score: {round(c['planner_score'] * 100)} of 100")
        if (change := _plan_change(c)):
            cmp = c["comparison"]
            facts.append(f"Plan change: {change} (estimated cost {cmp['cost_before']:,.0f} → {cmp['cost_after']:,.0f})")
        facts += c.get("rationale") or []
        if facts:
            b.append(("ul", facts))
    ruled_out = [c for c in candidates if c.get("dropped")]
    if ruled_out:
        b.append(("p", "Ruled out: " + "; ".join(f"{c['ddl']} ({c.get('drop_reason')})" for c in ruled_out)))

    b.append(("h2", "Benchmarks"))
    benchmarks = a.get("benchmarks") or []
    if not benchmarks:
        b.append(("p", "No benchmark has been run for this analysis, so no index idea has been measured."))
    for run in benchmarks:
        ddl = next((c["ddl"] for c in candidates if c["id"] == run.get("candidate_id")), "")
        b.append(("h3", ddl or f"Benchmark {run.get('id')}"))
        result = run.get("result")
        if run.get("status") != "completed" or not result:
            b.append(("p", f"Benchmark {run.get('status')}" + (f": {run['error']}" if run.get("error") else ".")))
            continue
        before, after = result["before"], result["after"]
        b.append(("table", ["Metric", "Original", "With index"], [
            ["Median", _ms(before["median_ms"]), _ms(after["median_ms"])],
            ["p95", _ms(before["p95_ms"]), _ms(after["p95_ms"])],
            ["Measured runs", str(before["n"]), str(after["n"])],
        ]))
        change = result["improvement_pct"]
        verdict = f"Median improvement: {_pct(change)} " + ("faster" if change >= 0 else "slower")
        facts = [verdict, f"Index size: {_bytes(result['index_size_bytes'])}", f"Index build time: {_ms(result['index_build_ms'])}"]
        if (plan_change := _plan_change(next((c for c in candidates if c["id"] == run.get("candidate_id")), None))):
            facts.append(f"Plan change: {plan_change}")
        facts.append("Query output: identical with and without the index" if result["checksum_match"] else "Query output: DIFFERENT with the index")
        facts += [f"Database: {result['pg_version']}", f"Dataset snapshot: {result['dataset_snapshot']}"]
        b.append(("ul", facts))
        if result.get("warnings"):
            b.append(("p", "Reliability warnings:"))
            b.append(("ul", list(result["warnings"])))

    runs = next((r["result"]["before"]["n"] for r in benchmarks if r.get("result")), 7)
    b += [
        ("h2", "Methodology"),
        ("p",
         f"Each index idea is benchmarked in an isolated copy of the sample database, never in the database that was analyzed. "
         f"The original query is warmed up once and then timed in {runs} measured runs; the real index is then created, the "
         "table statistics are refreshed, and the same query with the same parameters is timed in the same way. Reported "
         "figures are medians and 95th percentiles of EXPLAIN (ANALYZE) execution times, which include a small, equal "
         "instrumentation overhead on both sides. Result checksums are compared so a faster but different answer is never "
         "reported as an improvement. Nothing is applied automatically."),
    ]
    return b


def render_report(analysis: dict[str, Any]) -> str:
    out: list[str] = []
    for block in _blocks(analysis):
        kind = block[0]
        if kind in ("h1", "h2", "h3"):
            out.append(f"{'#' * int(kind[1])} {block[1]}")
        elif kind == "p":
            out.append(block[1])
        elif kind == "code":
            out.append(f"```sql\n{block[1]}\n```")
        elif kind == "ul":
            out.append("\n".join(f"- {item}" for item in block[1]))
        elif kind == "table":
            header, rows = block[1], block[2]
            lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
            lines += ["| " + " | ".join(r) + " |" for r in rows]
            out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"


_CSS = """
body{font:15px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#14202a}
h1{font-size:1.6rem}h2{margin-top:2rem;border-bottom:1px solid #d6ddda;padding-bottom:.25rem}h3{margin-bottom:.25rem}
pre{background:#e8edec;padding:.75rem;overflow:auto;border-radius:6px;font-size:13px}
table{border-collapse:collapse}td,th{border:1px solid #d6ddda;padding:.35rem .7rem;text-align:left}
code{font-family:ui-monospace,Menlo,Consolas,monospace}
"""


def render_report_html(analysis: dict[str, Any]) -> str:
    e = html.escape
    parts: list[str] = []
    for block in _blocks(analysis):
        kind = block[0]
        if kind in ("h1", "h2", "h3"):
            parts.append(f"<{kind}>{e(block[1])}</{kind}>")
        elif kind == "p":
            parts.append(f"<p>{e(block[1])}</p>")
        elif kind == "code":
            parts.append(f"<pre><code>{e(block[1])}</code></pre>")
        elif kind == "ul":
            parts.append("<ul>" + "".join(f"<li>{e(i)}</li>" for i in block[1]) + "</ul>")
        elif kind == "table":
            head = "".join(f"<th>{e(h)}</th>" for h in block[1])
            rows = "".join("<tr>" + "".join(f"<td>{e(c)}</td>" for c in r) + "</tr>" for r in block[2])
            parts.append(f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>")
    title = e(f"QueryDoctor report: analysis {analysis.get('id')}")
    return (
        f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title}</title><style>{_CSS}</style></head><body>\n" + "\n".join(parts) + "\n</body></html>\n"
    )
