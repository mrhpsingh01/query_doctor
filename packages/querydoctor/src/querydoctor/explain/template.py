"""The deterministic explainer: composes plain sentences from the evidence package, each citing the facts it used.

It is always available (no model, no network) and it is also the fallback whenever a language-model draft is rejected.
Sentences are built only from fact values, and the result is run through the same claim validator as a model's draft.
"""

from __future__ import annotations

from pydantic import BaseModel

from querydoctor.explain.facts import EvidencePackage, Fact, _num
from querydoctor.explain.validator import validate_claims

EMPTY = "There is not enough evidence to explain this analysis yet."


class Sentence(BaseModel):
    text: str
    fact_ids: list[str]


class Explanation(BaseModel):
    text: str
    sentences: list[Sentence]
    source: str  # "template" or "ollama:<model>"
    dropped: list[str] = []
    fallback_reason: str | None = None


class _Facts:
    def __init__(self, pkg: EvidencePackage) -> None:
        self.facts = pkg.facts

    def get(self, label: str) -> Fact | None:
        return next((f for f in self.facts if f.label == label), None)

    def starting(self, prefix: str) -> list[Fact]:
        return [f for f in self.facts if f.label.startswith(prefix)]


def _level(value: str) -> str:
    return value.replace("_", "-")


def _plan(f: _Facts) -> list[tuple[str, list[Fact]]]:
    out: list[tuple[str, list[Fact]]] = []
    exec_time, slow, slow_time, share = f.get("Execution time"), f.get("Slowest operator"), f.get("Slowest operator time"), f.get("Slowest operator share of execution time")
    tables = f.starting("Table read")
    if tables:
        names = ", ".join(str(t.value) for t in tables)
        out.append((f"The query reads {'the table' if len(tables) == 1 else 'the tables'} {names}.", tables))
    if exec_time:
        out.append((f"The query took {_num(exec_time.value)} ms to execute.", [exec_time]))
    if slow and slow_time and share:
        out.append((f"The slowest step was a {slow.value}, which took {_num(slow_time.value)} ms by itself, {_num(share.value)}% of the total.", [slow, slow_time, share]))
    seq = f.get("Sequential scans in the plan")
    if seq:
        out.append((f"The plan contains {_num(seq.value)} sequential scan(s), which read a whole table.", [seq]))
    collscan = f.get("Collection scans in the plan")
    if collscan:
        out.append((f"The plan contains {_num(collscan.value)} collection scan(s), which read every document of a collection.", [collscan]))
    cost, rows = f.get("Estimated plan cost"), f.get("Estimated rows returned")
    if not exec_time and cost and rows:
        out.append((f"PostgreSQL estimates the plan cost at {_num(cost.value)} and expects about {_num(rows.value)} rows to be returned.", [cost, rows]))
    total, critical = f.get("Findings"), f.get("Critical findings")
    if total and critical:
        out.append((f"The analysis raised {_num(total.value)} finding(s), {_num(critical.value)} of them critical.", [total, critical]))
    return out


def _findings(f: _Facts) -> list[tuple[str, list[Fact]]]:
    out: list[tuple[str, list[Fact]]] = []
    total, critical = f.get("Findings"), f.get("Critical findings")
    if total and critical:
        out.append((f"The analysis raised {_num(total.value)} finding(s), {_num(critical.value)} of them critical.", [total, critical]))
    for fact in f.facts:
        if fact.label.startswith("Finding "):
            rule = fact.label.removeprefix("Finding ")
            severity = f.get(f"{rule} severity")
            out.append((f"{fact.value} ({severity.value if severity else 'unrated'}).", [fact, *([severity] if severity else [])]))
        elif fact.label.split(" ")[0].startswith("PG_") and not fact.label.endswith("severity"):
            out.append((fact.text, [fact]))
    return out


def _ideas(f: _Facts, benchmarked: bool) -> list[tuple[str, list[Fact]]]:
    out: list[tuple[str, list[Fact]]] = []
    for ddl in f.starting("Index idea "):
        if not ddl.label.endswith(" DDL"):
            continue
        n = ddl.label.split(" ")[2]
        level = f.get(f"Index idea {n} evidence level")
        out.append((f"Index idea {n} is {ddl.value}, at the evidence level {_level(str(level.value)) if level else 'suggested'}.", [ddl, *([level] if level else [])]))
        cut = f.get(f"Index idea {n} estimated cost reduction")
        if cut:
            out.append((f"In a what-if test PostgreSQL estimates it cuts the plan cost by {_num(cut.value)}%.", [cut]))
        speed = next((p for p in f.starting("Predicted speedup") if p.label.endswith(f"idea {n})")), None)
        chance = next((p for p in f.starting("Predicted chance") if p.label.endswith(f"idea {n})")), None)
        if speed and chance and not benchmarked:
            out.append((f"A model predicts about {_num(speed.value)}x faster with a {_num(chance.value)}% chance of helping; this is a prediction, not a measurement.", [speed, chance]))
    return out


def _benchmark(f: _Facts) -> list[tuple[str, list[Fact]]]:
    before, after, gain = f.starting("Median before"), f.starting("Median after"), f.starting("Improvement")
    size, build, runs = f.starting("Index size"), f.starting("Index build time"), f.starting("Measured runs")
    if not (before and after and gain):
        return []
    b, a, g = before[0], after[0], gain[0]
    out = [(f"In the benchmark the median fell from {_num(b.value)} ms to {_num(a.value)} ms, which is {_num(g.value)}% faster.", [b, a, g])]
    if size and build:
        out.append((f"The price is an index of about {_num(size[0].value)} MB that took {_num(build[0].value)} ms to build.", [size[0], build[0]]))
    if runs:
        out.append((f"Each side was measured {_num(runs[0].value)} times.", [runs[0]]))
    return out


def _tradeoffs(f: _Facts) -> list[tuple[str, list[Fact]]]:
    bench = _benchmark(f)
    out = _ideas(f, benchmarked=bool(bench)) + bench
    if not bench and f.starting("Index idea "):
        out.append(("This index idea has not been benchmarked, so its real benefit and cost are unmeasured.", [f.starting("Index idea ")[0]]))
    return out


def _next_experiment(f: _Facts) -> list[tuple[str, list[Fact]]]:
    levels = [x for x in f.facts if x.label.endswith("evidence level")]
    if not levels:
        return _findings(f)
    top = levels[0]
    n = top.label.split(" ")[2]
    if top.value == "benchmark_validated":
        return [(f"Index idea {n} is already benchmark-validated: it was measured faster in an isolated copy. The next step is to review its index size and write cost before creating it.", [top])]
    return [(f"Index idea {n} is only {_level(str(top.value))}. Run a benchmark on it to measure whether it really helps before creating the index.", [top])]


def _pr_comment(f: _Facts) -> list[tuple[str, list[Fact]]]:
    parts = _benchmark(f)
    levels = [x for x in f.facts if x.label.endswith("evidence level")]
    if levels:
        n = levels[0].label.split(" ")[2]
        ddl = f.get(f"Index idea {n} DDL")
        parts.insert(0, (f"Suggested index: {ddl.value} (evidence level: {_level(str(levels[0].value))}).", [x for x in (ddl, levels[0]) if x]))
    if not parts:
        parts = _plan(f)[:3]
    return parts


_BUILDERS = {"plan": _plan, "findings": _findings, "tradeoffs": _tradeoffs, "next_experiment": _next_experiment, "pr_comment": _pr_comment}


def explain_template(pkg: EvidencePackage) -> Explanation:
    pairs = _BUILDERS[pkg.kind](_Facts(pkg))
    sentences = [Sentence(text=text, fact_ids=[x.id for x in used]) for text, used in pairs if used]
    cited = [f"{s.text} [{', '.join(s.fact_ids)}]" for s in sentences]
    # belt and braces: the template is held to the same standard as a model's draft
    kept = [(s, c) for s, c in zip(sentences, cited, strict=True) if validate_claims(c, pkg).ok]
    sentences, cited = [s for s, _c in kept], [c for _s, c in kept]
    if not sentences:
        return Explanation(text=EMPTY, sentences=[], source="template")
    body = " ".join(cited)
    if pkg.kind == "pr_comment":
        body = "**QueryDoctor** " + body
    return Explanation(text=body, sentences=sentences, source="template")
