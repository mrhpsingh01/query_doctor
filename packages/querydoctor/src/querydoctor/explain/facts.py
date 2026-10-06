"""The evidence behind an explanation: a numbered list of facts drawn only from computed analysis data.

An explanation (template or language model) may state only what these facts say. Facts never carry query parameters,
returned rows or connection details: only schema names, plan operators, measurements and index definitions.

A fact's ``derivation`` documents how its value relates to the analysis data: ``None`` (verbatim), ``percent`` (a ratio
shown as a percentage), ``ratio_percent`` (a quotient of two measurements as a percentage), ``bytes_to_mb`` or
``count`` (a number of items).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

KINDS = ("plan", "findings", "tradeoffs", "next_experiment", "pr_comment")
GROUPS_BY_KIND: dict[str, tuple[str, ...]] = {
    "plan": ("plan", "findings"),
    "findings": ("plan_min", "findings"),
    "tradeoffs": ("candidates", "benchmark"),
    "next_experiment": ("findings", "candidates", "benchmark"),
    "pr_comment": ("plan", "findings", "candidates", "benchmark"),
}
MAX_IDEAS = 3
_MB = 1024 * 1024


class Fact(BaseModel):
    id: str
    label: str
    value: float | int | str
    unit: str | None = None
    text: str
    derivation: str | None = None


class EvidencePackage(BaseModel):
    kind: str
    facts: list[Fact]

    def by_id(self) -> dict[str, Fact]:
        return {f.id: f for f in self.facts}

    def numeric_values(self) -> list[tuple[str, float | int, str | None]]:
        return [(f.id, f.value, f.unit) for f in self.facts if not isinstance(f.value, str)]


def _num(value: float | int) -> str:
    if isinstance(value, int) or float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


class _Collector:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, Any]]] = []

    def add(self, group: str, label: str, value: float | int | str, unit: str | None = None, *, text: str | None = None,
            derivation: str | None = None) -> None:
        if isinstance(value, bool) or value is None:
            return
        if text is None:
            text = f"{label}: {value}" if isinstance(value, str) else f"{label}: {_num(value)}{' ' + unit if unit else ''}."
        self.items.append((group, {"label": label, "value": value, "unit": unit, "text": text, "derivation": derivation}))


def _walk(node: dict) -> list[dict]:
    out = [node]
    for child in node.get("children") or []:
        out += _walk(child)
    return out


def _plan_facts(c: _Collector, analysis: dict) -> None:
    plan = analysis.get("plan")
    if not plan:
        return
    nodes = _walk(plan["root"])
    for table in sorted({n["relation_name"] for n in nodes if n.get("relation_name")}):
        c.add("plan_min", "Table read", table, text=f"The query reads the table {table}.")
    executed = bool(plan.get("executed")) and plan.get("execution_time_ms") is not None
    if executed:
        c.add("plan_min", "Execution time", plan["execution_time_ms"], "ms",
              text=f"The query took {_num(plan['execution_time_ms'])} ms to execute.")
        if plan.get("planning_time_ms") is not None:
            c.add("plan", "Planning time", plan["planning_time_ms"], "ms", text=f"Planning took {_num(plan['planning_time_ms'])} ms.")
    c.add("plan", "Plan operators", len(nodes), text=f"The plan has {len(nodes)} operators.", derivation="count")
    c.add("plan", "Sequential scans in the plan", sum(1 for n in nodes if n["node_type"] == "Seq Scan"), derivation="count",
          text=f"The plan has {sum(1 for n in nodes if n['node_type'] == 'Seq Scan')} sequential scan(s).")
    root = plan["root"]
    c.add("plan", "Estimated plan cost", root["estimated_cost"], text=f"PostgreSQL estimates the plan cost at {_num(root['estimated_cost'])}.")
    c.add("plan", "Estimated rows returned", root["estimated_rows"], "rows",
          text=f"PostgreSQL expects about {_num(root['estimated_rows'])} rows to be returned.")
    if executed:
        timed = [n for n in nodes if n.get("exclusive_time_ms") is not None]
        if timed:
            slow = max(timed, key=lambda n: n["exclusive_time_ms"])
            where = f" on {slow['relation_name']}" if slow.get("relation_name") else ""
            c.add("plan", "Slowest operator", slow["node_type"], text=f"The slowest operator was a {slow['node_type']}{where}.")
            c.add("plan", "Slowest operator time", slow["exclusive_time_ms"], "ms",
                  text=f"That operator itself took {_num(slow['exclusive_time_ms'])} ms.")
            if plan["execution_time_ms"]:
                share = 100 * slow["exclusive_time_ms"] / plan["execution_time_ms"]
                c.add("plan", "Slowest operator share of execution time", share, "%", derivation="ratio_percent",
                      text=f"That is {_num(share)}% of the execution time.")


def _unit_for(key: str, value: float) -> tuple[str | None, str | None]:
    if key.endswith("_ms"):
        return "ms", None
    if "rows" in key or "removed" in key:
        return "rows", None
    if key.endswith(("waste", "share", "fraction")) or (isinstance(value, float) and 0 <= value <= 1 and "ratio" not in key):
        return "%", "percent"
    return None, None


def _finding_facts(c: _Collector, analysis: dict) -> None:
    findings = analysis.get("findings") or []
    if not findings:
        return
    critical = sum(1 for f in findings if f.get("severity") == "critical")
    c.add("findings", "Findings", len(findings), derivation="count", text=f"The analysis produced {len(findings)} finding(s).")
    c.add("findings", "Critical findings", critical, derivation="count", text=f"{critical} of them are critical.")
    for f in findings:
        rule = f["rule_id"]
        c.add("findings", f"Finding {rule}", f["title"], text=f"Finding: {f['title']}.")
        c.add("findings", f"{rule} severity", f["severity"], text=f"Its severity is {f['severity']}.")
        for key, value in (f.get("evidence") or {}).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            unit, derivation = _unit_for(key, value)
            shown = value * 100 if derivation == "percent" else value
            readable = key.replace("_", " ")
            c.add("findings", f"{rule} {key}", shown, unit, derivation=derivation,
                  text=f"{readable.capitalize()}: {_num(shown)}{' ' + unit if unit else ''}.")


def _candidate_facts(c: _Collector, analysis: dict) -> list[dict]:
    ideas = sorted((x for x in analysis.get("candidates") or [] if not x.get("dropped")), key=lambda x: x.get("rank", 0))[:MAX_IDEAS]
    for i, idea in enumerate(ideas, start=1):
        c.add("candidates", f"Index idea {i} DDL", idea["ddl"], text=f"Index idea {i}: {idea['ddl']}.")
        c.add("candidates", f"Index idea {i} evidence level", idea["validation"],
              text=f"Index idea {i} has the evidence level {idea['validation'].replace('_', '-')}.")
        if idea.get("planner_score"):
            c.add("candidates", f"Index idea {i} planner score", idea["planner_score"],
                  text=f"The planner scores index idea {i} at {_num(idea['planner_score'])} on a scale from 0 to 1.")
        cmp = idea.get("comparison")
        if cmp:
            c.add("candidates", f"Index idea {i} estimated cost reduction", cmp["cost_reduction"] * 100, "%", derivation="percent",
                  text=f"In a what-if test PostgreSQL estimates index idea {i} cuts the plan cost by {_num(cmp['cost_reduction'] * 100)}%.")
        pred = idea.get("prediction")
        if pred:
            c.add("candidates", f"Predicted speedup (model estimate, idea {i})", pred["expected_speedup"], "x",
                  text=f"A model estimates index idea {i} could make the query about {_num(pred['expected_speedup'])}x faster. This is a prediction, not a measurement.")
            c.add("candidates", f"Predicted chance of improvement (model estimate, idea {i})", pred["p_improve"] * 100, "%", derivation="percent",
                  text=f"The model puts the chance that index idea {i} helps at {_num(pred['p_improve'] * 100)}%.")
    dropped = sum(1 for x in analysis.get("candidates") or [] if x.get("dropped"))
    if dropped:
        c.add("candidates", "Index ideas ruled out", dropped, derivation="count", text=f"{dropped} other index idea(s) were ruled out.")
    return ideas


def _benchmark_facts(c: _Collector, analysis: dict, ideas: list[dict]) -> None:
    index_of = {idea["id"]: i for i, idea in enumerate(ideas, start=1)}
    latest: dict[int, dict] = {}
    for run in analysis.get("benchmarks") or []:
        if run.get("status") == "completed" and run.get("result"):
            latest[run["candidate_id"]] = run
    for candidate_id, run in latest.items():
        r, i = run["result"], index_of.get(candidate_id)
        tag = " (benchmark)" if i is None or len(latest) == 1 else f" (benchmark, idea {i})"

        def label(base: str, tag: str = tag) -> str:
            return base + tag

        c.add("benchmark", label("Median before"), r["before"]["median_ms"], "ms", text=f"Measured median before the index: {_num(r['before']['median_ms'])} ms.")
        c.add("benchmark", label("Median after"), r["after"]["median_ms"], "ms", text=f"Measured median with the index: {_num(r['after']['median_ms'])} ms.")
        c.add("benchmark", label("p95 before"), r["before"]["p95_ms"], "ms", text=f"Measured 95th percentile before: {_num(r['before']['p95_ms'])} ms.")
        c.add("benchmark", label("p95 after"), r["after"]["p95_ms"], "ms", text=f"Measured 95th percentile with the index: {_num(r['after']['p95_ms'])} ms.")
        c.add("benchmark", label("Improvement"), r["improvement_pct"], "%", text=f"The index made the query {_num(r['improvement_pct'])}% faster in the benchmark.")
        c.add("benchmark", label("Index size"), r["index_size_bytes"] / _MB, "MB", derivation="bytes_to_mb",
              text=f"The index takes about {_num(r['index_size_bytes'] / _MB)} MB.")
        c.add("benchmark", label("Index build time"), r["index_build_ms"], "ms", text=f"Building the index took {_num(r['index_build_ms'])} ms.")
        c.add("benchmark", label("Measured runs per side"), r["before"]["n"], text=f"Each side was measured {r['before']['n']} times.")
        c.add("benchmark", label("Benchmark warnings"), len(r.get("warnings") or []), derivation="count",
              text=f"The benchmark raised {len(r.get('warnings') or [])} warning(s).")


def build_evidence(analysis: dict, kind: str) -> EvidencePackage:
    """The facts an explanation of ``kind`` may use, numbered F1..Fn in a stable order."""
    if kind not in GROUPS_BY_KIND:
        raise ValueError(f"unknown explanation kind {kind!r}; expected one of {list(KINDS)}")
    c = _Collector()
    _plan_facts(c, analysis)
    _finding_facts(c, analysis)
    ideas = _candidate_facts(c, analysis)
    _benchmark_facts(c, analysis, ideas)
    wanted = GROUPS_BY_KIND[kind]
    # plan_min facts are a subset of what the plan group would add, so the full plan facts include them exactly once
    selected = [item for group, item in c.items if group in wanted or (group == "plan_min" and "plan" in wanted)]
    facts = [Fact(id=f"F{n}", **item) for n, item in enumerate(selected, start=1)]
    return EvidencePackage(kind=kind, facts=facts)
