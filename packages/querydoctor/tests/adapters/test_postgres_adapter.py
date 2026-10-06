import json
import pathlib
from types import SimpleNamespace

import pytest

from querydoctor.adapters import DatabaseAdapter, PostgresAdapter, UnknownEngine, get_adapter, register_adapter, unregister_adapter
from querydoctor.benchmark import BenchmarkConfig
from querydoctor.candidates import generate_candidates, prune_redundant
from querydoctor.diagnosis import AnalysisContext, Thresholds, run_diagnosis
from querydoctor.plan_parser import ParsedPlan, PlanNode, parse_explain
from querydoctor.safety import ValidationError, validate_query
from querydoctor.schema import IndexInfo, SchemaSnapshot, TableInfo

FIXTURES = pathlib.Path(__file__).resolve().parents[4] / "fixtures" / "postgres_plans"
SQL = "SELECT id, total_amount FROM orders WHERE customer_id = $1 AND created_at >= $2 ORDER BY created_at DESC"


def raw(name="live_recent_orders_by_customer"):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def schema():
    return SchemaSnapshot(
        tables={"orders": TableInfo(name="orders", row_estimate=100_000, columns={"id": "integer", "customer_id": "integer", "total_amount": "numeric", "created_at": "timestamp"})},
        indexes={"orders": [IndexInfo(name="orders_pkey", table="orders", columns=["id"], unique=True)]},
    )


def test_the_postgres_adapter_satisfies_the_adapter_protocol():
    adapter = PostgresAdapter()
    assert isinstance(adapter, DatabaseAdapter) and adapter.engine == "postgres"


def test_validation_is_the_existing_validator():
    adapter = PostgresAdapter()
    assert adapter.validate_query(SQL, [1, "2022-01-01"], max_chars=20_000) == validate_query(SQL)
    with pytest.raises(ValidationError):
        adapter.validate_query("DROP TABLE orders", [], max_chars=20_000)
    with pytest.raises(ValidationError) as exc:
        adapter.validate_query(SQL, [1], max_chars=20_000)  # one parameter short
    assert exc.value.code == "param_mismatch"


def test_normalizing_and_diagnosing_give_the_same_results_as_the_direct_functions():
    adapter = PostgresAdapter()
    plan = adapter.normalize_plan(raw())
    assert plan == parse_explain(raw())
    assert isinstance(plan, ParsedPlan) and plan.engine == "postgres"
    vq = validate_query(SQL)
    thresholds = Thresholds(seq_scan_min_rows=50_000)
    direct = run_diagnosis(AnalysisContext(plan=plan, schema_=schema(), query=vq, thresholds=thresholds))
    assert adapter.diagnose(plan, schema(), vq, thresholds) == direct and direct


def test_candidate_generation_is_generation_then_pruning():
    adapter = PostgresAdapter()
    vq = validate_query(SQL)
    kept, pruned = adapter.generate_candidates(vq, schema(), [])
    direct_kept, direct_pruned = prune_redundant(generate_candidates(SQL, schema(), []), schema())
    assert kept == direct_kept and pruned == direct_pruned and kept


def test_the_injected_callables_receive_what_the_pipeline_used_to_pass_them():
    calls = []
    adapter = PostgresAdapter(
        explain=lambda conn, sql, params, *, execute: calls.append(("explain", sql, list(params), execute)) or raw(),
        collect_schema=lambda conn, tables: calls.append(("schema", list(tables))) or schema(),
        scorer=lambda conn, sql, params, plan, cands, rows: calls.append(("score", sql, list(params), len(cands), rows)) or ["scored"],
        benchmark_runner=lambda dsn, sql, params, ddl, config, **kw: calls.append(("bench", dsn, ddl, config.runs, sorted(kw))) or "result",
    )
    vq = validate_query(SQL)
    assert adapter.explain("conn", vq, [1, "x"], execute=True) == raw()
    assert adapter.collect_schema("conn", vq) == schema()
    kept, _ = adapter.generate_candidates(vq, schema(), [])
    assert adapter.test_candidates("conn", vq, [1, "x"], adapter.normalize_plan(raw()), kept, schema()) == ["scored"]
    assert adapter.benchmark("dsn", vq, [1, "x"], SimpleNamespace(ddl="CREATE INDEX ON orders (id)"), BenchmarkConfig(), on_progress=print, cancelled=lambda: False, template="t") == "result"
    assert calls[0] == ("explain", vq.sql, [1, "x"], True)
    assert calls[1] == ("schema", ["orders"])
    assert calls[2][:4] == ("score", vq.sql, [1, "x"], len(kept)) and calls[2][4] == {"orders": 100_000}
    assert calls[3] == ("bench", "dsn", "CREATE INDEX ON orders (id)", 7, ["cancelled", "on_progress", "template"])


def test_workload_collection_reads_and_merges_pg_stat_statements(monkeypatch):
    import querydoctor.adapters.postgres as module

    monkeypatch.setattr(module, "read_statements", lambda conn, limit: ["row"] * limit)
    monkeypatch.setattr(module, "merge_statements", lambda rows: ([len(rows)], 2))
    assert PostgresAdapter().collect_workload("conn", 5) == ([5], 2)


def test_the_registry_knows_postgres_and_refuses_engines_nobody_registered():
    assert isinstance(get_adapter("postgres"), PostgresAdapter)
    with pytest.raises(UnknownEngine):
        get_adapter("oracle")
    sentinel = object()
    register_adapter("fake", lambda **ctx: sentinel)
    try:
        assert get_adapter("fake") is sentinel
    finally:
        unregister_adapter("fake")
    with pytest.raises(UnknownEngine):
        get_adapter("fake")


def test_the_registry_passes_context_to_the_factory():
    adapter = get_adapter("postgres", explain=lambda *a, **k: "x")
    assert adapter.explain(None, validate_query(SQL), [], execute=False) == "x"


def test_plans_and_nodes_carry_the_engine_and_mongo_counters_with_postgres_defaults():
    plan = parse_explain(raw())
    assert plan.engine == "postgres"
    assert plan.root.docs_examined is None and plan.root.keys_examined is None
    node = PlanNode(node_id="0", parent_id=None, node_type="COLLSCAN", estimated_rows=0, estimated_cost=0, docs_examined=500, keys_examined=0)
    assert node.docs_examined == 500 and node.keys_examined == 0
