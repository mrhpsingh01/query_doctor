"""Build the labelled dataset the recommendation ranker learns from.

For each *query instance* (a template with sampled parameters, on one data variant): plan it, generate and prune index
candidates, add deliberately useless decoys, score all of them with HypoPG (planner only), extract features, then
**measure** each candidate for real in a throwaway clone with one shared baseline (``measure_candidates``). A row is one
(query instance, candidate) pair: planner-only features plus measured labels.

``group_id`` is the query fingerprint, shared by a template across every variant and parameter choice, so a group-based
split never puts the same query shape on both sides. ``query_id`` is ``fingerprint:variant:parameter-hash`` and groups
the candidates of one concrete query for ranking metrics.
"""

from __future__ import annotations

import json
import logging
import math
import pathlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import psycopg
from pydantic import BaseModel

from querydoctor.benchmark import BenchmarkConfig
from querydoctor.benchmark.runner import BenchmarkFailed, measure_candidates
from querydoctor.benchmark.sandbox import Sandbox
from querydoctor.candidates import IndexCandidate, TableUsage, extract_usage, generate_candidates, prune_redundant, score_candidates
from querydoctor.candidates.generate import _candidate, _key_columns
from querydoctor.candidates.models import IndexColumn
from querydoctor.db import DbError, explain, guarded_connection
from querydoctor.db.schema_collect import collect_schema
from querydoctor.diagnosis import AnalysisContext, Thresholds, run_diagnosis
from querydoctor.lab.query_templates import QueryInstance
from querydoctor.plan_parser import parse_explain
from querydoctor.ranking.features import FEATURE_VERSION, FeatureInput, extract_features
from querydoctor.safety import ValidationError, validate_query
from querydoctor.schema import SchemaSnapshot

log = logging.getLogger(__name__)

USEFUL_PCT = 20.0  # a candidate is "useful" when it makes the query at least this much faster
REGRESSION_SPEEDUP = 0.9  # before / after below this means the index made the query slower
MAX_GENERATED = 6
MAX_DECOYS = 3
_BYTES_PER_MB = 1024 * 1024


class DatasetRow(BaseModel):
    features: dict[str, float]
    log_speedup: float  # ln(before median / after median)
    improvement_pct: float
    useful: bool
    regression: bool
    index_size_mb: float
    build_ms: float
    group_id: str
    query_id: str
    variant: str
    template_id: str
    ddl: str
    baseline_ms: float
    candidate_ms: float
    planner_score: float
    uses_candidate: bool  # the planner picked the candidate in the what-if plan
    index_used: bool  # the real index appeared in the measured final plan
    checksum_match: bool
    is_decoy: bool
    feature_version: int


@dataclass(frozen=True)
class VariantEnv:
    bench_dsn: str  # the bench role's connection (clones databases)
    variant: str
    template: str  # the variant's template database


def write_rows(path: str | pathlib.Path, rows: Iterable[DatasetRow], *, append: bool = False) -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if append else "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(r.model_dump_json() + "\n")


def read_rows(path: str | pathlib.Path) -> list[DatasetRow]:
    path = pathlib.Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [DatasetRow.model_validate(json.loads(line)) for line in f if line.strip()]


def _used_columns(usage: TableUsage) -> set[str]:
    return {*usage.equality, *usage.range, *usage.join, *usage.group_by, *usage.output, *(c for c, _ in usage.order_by)}


def decoys(usage: dict[str, TableUsage], schema: SchemaSnapshot) -> list[IndexCandidate]:
    """Indexes that are plausible-looking but should not help: the ranker has to see failures too.

    Per table: the key columns in the wrong order, only the leading column of a good composite, and an index on a column
    the query never touches. None of them duplicates a candidate ``generate_candidates`` would produce.
    """
    out: dict[str, IndexCandidate] = {}
    for table, u in usage.items():
        info = schema.tables.get(table)
        if info is None:
            continue
        key = _key_columns(u)

        def add(columns: list[IndexColumn], why: str) -> None:
            candidate = _candidate(table, columns, [], None, None, [f"decoy: {why}"], [])
            out.setdefault(candidate.ddl, candidate)

        if len(key) >= 2:
            add(list(reversed(key)), "the key columns in the wrong order")
            add(key[:1], "only the leading column of a good composite index")
        untouched = sorted(c for c in info.columns if c != "id" and c not in _used_columns(u))
        if untouched:
            add([IndexColumn(name=untouched[0])], "a column the query never touches")
    return list(out.values())


def _label(env: VariantEnv, inst: QueryInstance, scored, features, result, *, decoy: bool) -> DatasetRow:
    before, after = result.before.median_ms, result.after.median_ms
    speedup = max(before, 1e-6) / max(after, 1e-6)
    return DatasetRow(
        features=features, log_speedup=math.log(speedup), improvement_pct=result.improvement_pct,
        useful=result.improvement_pct >= USEFUL_PCT and result.checksum_match, regression=speedup < REGRESSION_SPEEDUP,
        index_size_mb=result.index_size_bytes / _BYTES_PER_MB, build_ms=result.index_build_ms, group_id=inst.fingerprint,
        query_id=inst.query_id, variant=inst.variant, template_id=inst.template_id, ddl=scored.candidate.ddl,
        baseline_ms=before, candidate_ms=after, planner_score=scored.comparison.planner_score,
        uses_candidate=scored.comparison.uses_candidate, index_used=result.index_used, checksum_match=result.checksum_match,
        is_decoy=decoy, feature_version=FEATURE_VERSION,
    )


def _rows_for_instance(conn, env: VariantEnv, inst: QueryInstance, config: BenchmarkConfig) -> list[DatasetRow]:
    vq = validate_query(inst.sql)
    raw = explain(conn, inst.sql, inst.params, execute=False)
    original = parse_explain(raw)
    schema = collect_schema(conn, vq.tables, extras=True)
    findings = run_diagnosis(AnalysisContext(plan=original, schema_=schema, query=vq, thresholds=Thresholds()))
    usage = extract_usage(inst.sql, schema)

    generated, _pruned = prune_redundant(generate_candidates(inst.sql, schema, findings), schema)
    real = generated[:MAX_GENERATED]
    taken = {c.ddl for c in real}
    fakes = [d for d in decoys(usage, schema) if d.ddl not in taken][:MAX_DECOYS]
    decoy_ddls = {d.ddl for d in fakes}
    candidates = [*real, *fakes]
    if not candidates:
        return []

    table_rows = {name: t.row_estimate for name, t in schema.tables.items()}
    scored = {
        s.candidate.ddl: s
        for s in score_candidates(conn, inst.sql, inst.params, original, candidates, table_rows)
        if s.error is None
    }
    if not scored:
        return []
    measurements = measure_candidates(env.bench_dsn, inst.sql, inst.params, list(scored), config, template=env.template)

    rows: list[DatasetRow] = []
    for m in measurements:
        if m.result is None:
            log.info("skipping %s: %s", m.ddl, m.error)
            continue
        s = scored[m.ddl]
        features = extract_features(
            FeatureInput(
                sql=inst.sql, usage=usage, schema=schema, original=original, candidate=s.candidate, candidate_plan=s.plan,
                comparison=s.comparison,
            )
        )
        rows.append(_label(env, inst, s, features, m.result, decoy=m.ddl in decoy_ddls))
    return rows


def build_rows(
    env: VariantEnv, instances: Iterable[QueryInstance], config: BenchmarkConfig, *, on_instance=lambda inst, rows: None
) -> Iterator[DatasetRow]:
    """Yield dataset rows for each instance as soon as its candidates are measured. An instance that fails (an
    unplannable query, a busy bench instance) is logged and skipped; the rest continue."""
    with Sandbox(env.bench_dsn, env.template) as explore:  # a clone for planner-only work (HypoPG, schema, plans)
        with guarded_connection(explore.dsn, role_kind="bench_admin", statement_timeout_ms=30_000) as conn:
            for inst in instances:
                try:
                    rows = _rows_for_instance(conn, env, inst, config)
                except (BenchmarkFailed, DbError, ValidationError, ValueError, psycopg.Error) as exc:
                    log.warning("instance %s (%s) skipped: %s", inst.query_id, inst.template_id, exc)
                    conn.rollback()
                    continue
                on_instance(inst, rows)
                yield from rows
