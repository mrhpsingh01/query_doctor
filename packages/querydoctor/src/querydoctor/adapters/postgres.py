"""The PostgreSQL adapter: a thin wrapper over the existing functions, so behaviour is exactly what it was."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from querydoctor.benchmark.runner import run_benchmark
from querydoctor.candidates import generate_candidates, prune_redundant, score_candidates
from querydoctor.db import collect_schema, explain
from querydoctor.db.errors import ParamMismatch
from querydoctor.db.params import check_params
from querydoctor.diagnosis import AnalysisContext, run_diagnosis
from querydoctor.plan_parser import parse_explain
from querydoctor.safety import ValidationError, validate_query
from querydoctor.workload import merge_statements, read_statements


class PostgresAdapter:
    engine = "postgres"

    def __init__(
        self,
        *,
        explain: Callable | None = None,
        collect_schema: Callable | None = None,
        scorer: Callable | None = None,
        benchmark_runner: Callable | None = None,
    ) -> None:
        # replaceable so the pipeline's existing test doubles keep working unchanged
        self._explain = explain or _default_explain
        self._collect_schema = collect_schema or _default_collect_schema
        self._scorer = scorer or score_candidates
        self._benchmark = benchmark_runner or run_benchmark

    def validate_query(self, text: str, params: Sequence[Any], *, max_chars: int):
        vq = validate_query(text, max_chars=max_chars)
        try:
            check_params(params)
        except ParamMismatch as exc:
            raise ValidationError("param_mismatch", str(exc)) from exc
        if len(params) != vq.param_count:
            raise ValidationError("param_mismatch", f"query uses {vq.param_count} parameter(s) but {len(params)} were supplied")
        return vq

    def collect_schema(self, conn, vq):
        return self._collect_schema(conn, vq.tables)

    def explain(self, conn, vq, params: Sequence[Any], *, execute: bool):
        return self._explain(conn, vq.sql, params, execute=execute)

    def normalize_plan(self, raw):
        return parse_explain(raw)

    def diagnose(self, plan, schema, vq, thresholds):
        return run_diagnosis(AnalysisContext(plan=plan, schema_=schema, query=vq, thresholds=thresholds))

    def generate_candidates(self, vq, schema, findings):
        return prune_redundant(generate_candidates(vq.sql, schema, list(findings)), schema)

    def test_candidates(self, conn, vq, params: Sequence[Any], baseline, candidates: Sequence[Any], schema):
        table_rows = {name: info.row_estimate for name, info in schema.tables.items()}
        return self._scorer(conn, vq.sql, params, baseline, candidates, table_rows)

    def benchmark(self, dsn: str, vq, params: Sequence[Any], candidate_ddl: str, config, *, on_progress, cancelled, template: str | None = None):
        extra = {} if template is None else {"template": template}  # the default template is the sample database
        return self._benchmark(dsn, vq.sql, params, candidate_ddl, config, on_progress=on_progress, cancelled=cancelled, **extra)

    def collect_workload(self, conn, limit: int):
        return merge_statements(read_statements(conn, limit))


def _default_explain(conn, sql, params, *, execute):
    return explain(conn, sql, params, execute=execute)


def _default_collect_schema(conn, tables):
    return collect_schema(conn, tables)
