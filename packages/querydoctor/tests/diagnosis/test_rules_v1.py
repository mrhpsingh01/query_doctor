import logging

import pytest
from helpers import make_ctx, node, schema_of

from querydoctor.diagnosis import ALL_RULES, Finding, run_diagnosis
from querydoctor.plan_parser import parse_explain

RULES = {r.rule_id: r for r in ALL_RULES}


def evaluate(rule_id, root, **ctx_kwargs):
    """Run one rule through the engine (so node-type gating applies); returns its first finding or None."""
    ctx = make_ctx(root, **ctx_kwargs)
    findings = run_diagnosis(ctx, rules=[RULES[rule_id]])
    return findings[0] if findings else None


# ---- PG_SEQ_SCAN_FILTER_WASTE -------------------------------------------------------------------------------------

def test_seq_scan_waste_critical_with_evidence():
    f = evaluate("PG_SEQ_SCAN_FILTER_WASTE", node(relation="orders", rows=10_842, removed=1_813_561, filter="(a = 1)"))
    assert isinstance(f, Finding)
    assert f.severity == "critical"
    assert f.node_ids == ["0"]
    assert f.evidence["rows_visited"] == 10_842 + 1_813_561
    assert f.evidence["rows_removed"] == 1_813_561
    assert f.evidence["filter_waste"] == pytest.approx(0.9941, abs=1e-4)
    assert f.confidence >= 0.9
    assert "99.4%" in f.summary


def test_seq_scan_waste_warning_between_thresholds():
    f = evaluate("PG_SEQ_SCAN_FILTER_WASTE", node(rows=30_000, removed=170_000, filter="(a = 1)"))
    assert f.severity == "warning"  # 85% of 200k


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(rows=5_000, removed=45_000),  # 50k rows visited: too small
        dict(rows=500_000, removed=500_000),  # 50% waste: not selective enough
        dict(rows=10, removed=0),
        dict(rows=0, removed=0, loops=0),  # never executed
    ],
)
def test_seq_scan_waste_negative(kwargs):
    assert evaluate("PG_SEQ_SCAN_FILTER_WASTE", node(**kwargs)) is None


def test_seq_scan_waste_ignores_other_node_types():
    root = node("Index Scan", rows=10, removed=1_000_000)
    assert evaluate("PG_SEQ_SCAN_FILTER_WASTE", root) is None


def test_seq_scan_waste_multiplies_loops():
    f = evaluate("PG_SEQ_SCAN_FILTER_WASTE", node(rows=1, removed=999, loops=500, filter="(a = 1)"))
    assert f is not None and f.evidence["rows_visited"] == 1_000 * 500
    # evidence is all in the same unit (totals across loops), so visited = returned + removed always holds
    assert f.evidence["rows_removed"] == 999 * 500
    assert f.evidence["loops"] == 500


def test_seq_scan_waste_explain_only_uses_schema_with_lower_confidence():
    root = node(relation="orders", est_rows=5_000, explain_only=True, filter="(status = 'x')")
    f = evaluate("PG_SEQ_SCAN_FILTER_WASTE", root, schema=schema_of(orders=1_000_000))
    assert f is not None
    assert f.confidence < 0.7
    assert f.severity == "warning"
    assert f.evidence["estimated"] is True


def test_seq_scan_waste_explain_only_without_schema_is_silent():
    root = node(relation="orders", est_rows=5_000, explain_only=True, filter="(status = 'x')")
    assert evaluate("PG_SEQ_SCAN_FILTER_WASTE", root) is None


# ---- PG_HIGH_FILTER_WASTE -----------------------------------------------------------------------------------------

def test_high_filter_waste_on_index_scan():
    f = evaluate("PG_HIGH_FILTER_WASTE", node("Index Scan", index="idx", rows=1_000, removed=9_000, filter="(b = 2)"))
    assert f is not None and f.severity == "warning"
    assert f.evidence["filter_waste"] == pytest.approx(0.9)


def test_high_filter_waste_evidence_uses_totals_across_loops():
    f = evaluate("PG_HIGH_FILTER_WASTE", node("Index Scan", rows=10, removed=990, loops=20, filter="(b = 2)"))
    assert f.evidence["rows_visited"] == 1_000 * 20
    assert f.evidence["rows_removed"] == 990 * 20


def test_high_filter_waste_skips_seq_scan_and_small_inputs():
    assert evaluate("PG_HIGH_FILTER_WASTE", node("Seq Scan", rows=1, removed=1_000_000)) is None
    assert evaluate("PG_HIGH_FILTER_WASTE", node("Index Scan", rows=1, removed=50)) is None
    assert evaluate("PG_HIGH_FILTER_WASTE", node("Index Scan", rows=5_000, removed=5_000)) is None


# ---- PG_CARDINALITY_MISESTIMATE -----------------------------------------------------------------------------------

def test_cardinality_warning_and_critical():
    warn = evaluate("PG_CARDINALITY_MISESTIMATE", node(est_rows=100, rows=2_000))
    crit = evaluate("PG_CARDINALITY_MISESTIMATE", node(est_rows=100, rows=10_000))
    assert warn.severity == "warning" and crit.severity == "critical"
    assert crit.evidence["error_ratio"] == pytest.approx(100.0)
    assert crit.evidence["direction"] == "underestimated"
    over = evaluate("PG_CARDINALITY_MISESTIMATE", node(est_rows=50_000, rows=100))
    assert over.evidence["direction"] == "overestimated"


def test_cardinality_negative_cases():
    assert evaluate("PG_CARDINALITY_MISESTIMATE", node(est_rows=100, rows=500)) is None  # ratio 5
    assert evaluate("PG_CARDINALITY_MISESTIMATE", node(est_rows=1, rows=20)) is None  # tiny absolute error
    assert evaluate("PG_CARDINALITY_MISESTIMATE", node(est_rows=100, explain_only=True)) is None
    assert evaluate("PG_CARDINALITY_MISESTIMATE", node(est_rows=100, rows=0, loops=0)) is None


# ---- PG_REPEATED_NESTED_LOOP --------------------------------------------------------------------------------------

def test_repeated_nested_loop_flags_inner_node(load_plan):
    ctx_plan = parse_explain(load_plan("nested_loop_repeated"))
    from querydoctor.diagnosis import AnalysisContext

    ctx = AnalysisContext(plan=ctx_plan)
    inner = ctx_plan.root.children[1]
    f = RULES["PG_REPEATED_NESTED_LOOP"].evaluate(inner, ctx)
    assert f is not None and f.severity == "critical"
    assert f.node_ids == [inner.node_id]
    assert f.evidence["loops"] == 2000
    assert f.evidence["total_ms"] == pytest.approx(6000.0)
    assert RULES["PG_REPEATED_NESTED_LOOP"].evaluate(ctx_plan.root.children[0], ctx) is None


def test_repeated_nested_loop_fires_at_a_hundred_loops_when_time_dominates():
    root = node("Nested Loop", time=1000.0, plans=[node(loops=120, time=7.0)])
    f = evaluate("PG_REPEATED_NESTED_LOOP", root, execution_time=1000.0)
    assert f is not None and f.evidence["loops"] == 120


def test_repeated_nested_loop_few_loops_is_silent():
    root = node("Nested Loop", time=1000.0, plans=[node(loops=10, time=50.0)])
    assert evaluate("PG_REPEATED_NESTED_LOOP", root) is None


def test_repeated_nested_loop_low_time_share_is_silent():
    root = node("Nested Loop", time=60_000.0, plans=[node(loops=2_000, time=0.001)])
    ctx = make_ctx(root)
    assert RULES["PG_REPEATED_NESTED_LOOP"].evaluate(ctx.plan.root.children[0], ctx) is None


# ---- PG_SORT_SPILL ------------------------------------------------------------------------------------------------

def test_sort_spill_detected(load_plan):
    from querydoctor.diagnosis import AnalysisContext

    plan = parse_explain(load_plan("sort_spill"))
    f = RULES["PG_SORT_SPILL"].evaluate(plan.root, AnalysisContext(plan=plan))
    assert f is not None
    assert f.node_ids == ["0"]
    assert f.evidence["sort_method"] == "external merge"
    assert f.evidence["temp_written_blocks"] == 5120


def test_sort_in_memory_not_flagged():
    assert evaluate("PG_SORT_SPILL", node("Sort", sort_method="quicksort", temp_written=0)) is None
    assert evaluate("PG_SORT_SPILL", node("Seq Scan", temp_written=100)) is None  # not a Sort node


# ---- engine -------------------------------------------------------------------------------------------------------

def test_run_diagnosis_orders_by_severity_and_is_deterministic():
    root = node(
        "Sort", sort_method="external merge", temp_written=10, time=1000.0,
        plans=[node(relation="orders", rows=10_842, removed=1_813_561, filter="(a=1)", est_rows=100)],
    )
    ctx = make_ctx(root, execution_time=1000.0)
    findings = run_diagnosis(ctx)
    assert [f.severity for f in findings] == sorted([f.severity for f in findings], key=["critical", "warning", "info"].index)
    assert {f.rule_id for f in findings} >= {"PG_SEQ_SCAN_FILTER_WASTE", "PG_SORT_SPILL", "PG_CARDINALITY_MISESTIMATE"}
    assert [f.model_dump() for f in findings] == [f.model_dump() for f in run_diagnosis(ctx)]


def test_thresholds_are_overridable():
    root = node(rows=10, removed=990, filter="(a=1)")
    assert evaluate("PG_SEQ_SCAN_FILTER_WASTE", root) is None
    assert evaluate("PG_SEQ_SCAN_FILTER_WASTE", root, seq_scan_min_rows=500) is not None


def test_a_crashing_rule_does_not_stop_diagnosis(caplog):
    class Boom:
        rule_id = "BOOM"
        supported_node_types: set[str] = set()

        def evaluate(self, node, context):
            raise RuntimeError("kaboom")

    ctx = make_ctx(node(rows=10_842, removed=1_813_561, filter="(a=1)"))
    with caplog.at_level(logging.ERROR):
        findings = run_diagnosis(ctx, rules=[Boom(), *ALL_RULES])
    assert any(f.rule_id == "PG_SEQ_SCAN_FILTER_WASTE" for f in findings)
    assert "BOOM" in caplog.text


def test_explain_only_and_zero_row_plans_never_crash(load_plan):
    from querydoctor.diagnosis import AnalysisContext

    for name in ("explain_only", "seqscan_filter_waste", "nested_loop_repeated", "sort_spill"):
        run_diagnosis(AnalysisContext(plan=parse_explain(load_plan(name))))
    run_diagnosis(make_ctx(node(est_rows=0, rows=0, removed=0, loops=1, filter="(x=1)")))
