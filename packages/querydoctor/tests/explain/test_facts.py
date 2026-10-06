import copy
import json
import math
import pathlib

import pytest

from querydoctor.benchmark import summarize
from querydoctor.diagnosis import AnalysisContext, Thresholds, run_diagnosis
from querydoctor.explain import EvidencePackage, Fact, build_evidence
from querydoctor.plan_parser import parse_explain
from querydoctor.schema import SchemaSnapshot, TableInfo

FIXTURES = pathlib.Path(__file__).resolve().parents[4] / "fixtures" / "postgres_plans"
KINDS = ["plan", "findings", "tradeoffs", "next_experiment", "pr_comment"]
DSN = "postgresql://qd_readonly:s3cret@10.1.2.3:5432/shop"


def benchmark_result():
    before, after = [700.0, 742.0, 750.0, 735.0, 760.0, 740.0, 745.0], [30.0, 31.0, 29.0, 32.0, 30.5, 31.5, 30.0]
    return {
        "before": summarize(before).model_dump(), "after": summarize(after).model_dump(), "improvement_pct": 95.9,
        "index_size_bytes": 5_242_880, "index_build_ms": 68.0, "checksum_match": True, "index_used": True,
        "warnings": ["High variance in the original runs."], "pg_version": "PostgreSQL 16", "dataset_snapshot": "snap",
        "ddl": "CREATE INDEX ON orders (customer_id, created_at DESC)", "settings": {"work_mem": "4096"},
        "before_samples": [{"execution_ms": x} for x in before], "after_samples": [{"execution_ms": x} for x in after],
        "plan_fingerprint_before": "a", "plan_fingerprint_after": "b",
    }


def make_analysis(*, executed=True, benchmark="completed", prediction=True) -> dict:
    raw = json.loads((FIXTURES / "live_recent_orders_by_customer.json").read_text(encoding="utf-8"))
    plan = parse_explain(raw)
    schema = SchemaSnapshot(tables={"orders": TableInfo(name="orders", row_estimate=100_000, columns={"id": "integer"})})
    findings = [f.model_dump() | {"id": i + 1} for i, f in enumerate(run_diagnosis(AnalysisContext(plan=plan, schema_=schema, thresholds=Thresholds(seq_scan_min_rows=50_000))))]
    plan_dict = plan.model_dump()
    if not executed:
        plan_dict["executed"] = False
        plan_dict["execution_time_ms"] = None
        plan_dict["planning_time_ms"] = None

        def strip(node):
            for key in ("actual_rows", "actual_time_ms", "loops", "exclusive_time_ms", "total_time_ms", "rows_removed_by_filter", "filter_waste", "error_ratio"):
                node[key] = None
            for child in node["children"]:
                strip(child)

        strip(plan_dict["root"])
    run = {"id": 5, "analysis_id": 1, "candidate_id": 11, "status": benchmark, "result": benchmark_result() if benchmark == "completed" else None, "error": None}
    return {
        "id": 1, "status": "completed", "execute": executed, "sql": "SELECT id FROM orders WHERE customer_id = 4242", "params": [4242, "alice@example.com"],
        "plan": plan_dict, "execution_time_ms": plan_dict["execution_time_ms"], "findings": findings,
        "candidates": [
            {
                "id": 11, "ddl": "CREATE INDEX ON orders (customer_id, created_at DESC)", "table_name": "orders",
                "validation": "benchmark_validated" if benchmark == "completed" else "planner_validated", "planner_score": 0.95,
                "dropped": False, "rank": 1, "rationale": ["equality on customer_id"],
                "comparison": {"cost_reduction": 0.964, "cost_before": 61527.0, "cost_after": 2200.0, "removed_operators": ["Seq Scan"], "added_operators": ["Index Scan"], "uses_candidate": True},
                "prediction": {"expected_speedup": 8.2, "p_improve": 0.93, "expected_index_mb": 4.5, "p_regression": 0.02} if prediction else None,
            },
            {"id": 12, "ddl": "CREATE INDEX ON orders (status)", "table_name": "orders", "validation": "suggested", "planner_score": 0.0, "dropped": True, "rank": 0, "rationale": [], "comparison": None, "prediction": None},
        ],
        "benchmarks": [run] if benchmark else [],
        "connection_note": DSN,
    }


def numbers_in(obj) -> list[float]:
    found: list[float] = []
    if isinstance(obj, bool):
        return found
    if isinstance(obj, (int, float)):
        found.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            found += numbers_in(v)
    elif isinstance(obj, list):
        for v in obj:
            found += numbers_in(v)
    return found


def explained_by(fact: Fact, numbers: list[float], analysis: dict) -> bool:
    v = fact.value
    if isinstance(v, str):
        return v in json.dumps(analysis)
    if fact.derivation is None:
        return any(math.isclose(v, n, rel_tol=1e-9, abs_tol=1e-9) for n in numbers)
    if fact.derivation == "percent":  # a ratio in the data, shown as a percentage
        return any(math.isclose(v, n * 100, rel_tol=1e-9, abs_tol=1e-9) for n in numbers)
    if fact.derivation == "bytes_to_mb":
        return any(math.isclose(v, n / 1024 / 1024, rel_tol=1e-9, abs_tol=1e-9) for n in numbers)
    if fact.derivation == "count":
        return v == int(v) and v >= 0
    if fact.derivation == "ratio_percent":  # 100 x a quotient of two numbers in the data
        return any(math.isclose(v, 100 * a / b, rel_tol=1e-6) for a in numbers for b in numbers if b)
    if fact.derivation == "ratio":  # a quotient of two numbers in the data
        return any(math.isclose(v, a / b, rel_tol=1e-6) for a in numbers for b in numbers if b)
    return False


def test_facts_are_numbered_f1_to_fn_in_order_and_stable():
    a = make_analysis()
    for kind in KINDS:
        pkg = build_evidence(a, kind)
        assert isinstance(pkg, EvidencePackage) and pkg.kind == kind and pkg.facts
        assert [f.id for f in pkg.facts] == [f"F{i}" for i in range(1, len(pkg.facts) + 1)]
        assert [f.model_dump() for f in build_evidence(copy.deepcopy(a), kind).facts] == [f.model_dump() for f in pkg.facts]
        assert set(pkg.by_id()) == {f.id for f in pkg.facts}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("variant", [{}, {"executed": False}, {"benchmark": None}, {"benchmark": "failed"}])
def test_every_number_comes_from_the_analysis_or_a_documented_derivation(kind, variant):
    a = make_analysis(**variant)
    numbers = numbers_in(a)
    for fact in build_evidence(a, kind).facts:
        assert fact.label and fact.text and str(fact.value) != ""
        assert explained_by(fact, numbers, a), f"{kind}: {fact.id} {fact.label}={fact.value!r} ({fact.derivation}) has no source"


def test_a_planner_only_analysis_has_no_timing_facts():
    pkg = build_evidence(make_analysis(executed=False), "plan")
    labels = " ".join(f.label for f in pkg.facts).lower()
    assert "execution time" not in labels and "slowest operator" not in labels
    assert all(f.unit != "ms" for f in pkg.facts if "benchmark" not in f.label.lower())
    executed = build_evidence(make_analysis(), "plan")
    assert any("execution time" in f.label.lower() for f in executed.facts)
    assert any("slowest operator" in f.label.lower() for f in executed.facts)


def test_benchmark_facts_exist_only_for_a_completed_benchmark():
    done = build_evidence(make_analysis(benchmark="completed"), "tradeoffs")
    labels = [f.label.lower() for f in done.facts]
    for wanted in ("median before", "median after", "improvement", "index size", "index build"):
        assert any(wanted in label for label in labels), wanted
    for state in ("failed", None):
        pkg = build_evidence(make_analysis(benchmark=state), "tradeoffs")
        assert not any("median" in f.label.lower() for f in pkg.facts)


def test_benchmark_facts_carry_the_measured_values():
    pkg = build_evidence(make_analysis(), "tradeoffs")
    by = {f.label.lower(): f for f in pkg.facts}
    assert by["median before (benchmark)"].value == 742.0 and by["median before (benchmark)"].unit == "ms"
    assert by["improvement (benchmark)"].value == 95.9 and by["improvement (benchmark)"].unit == "%"
    assert by["index size (benchmark)"].value == 5.0 and by["index size (benchmark)"].unit == "MB"
    assert by["index size (benchmark)"].derivation == "bytes_to_mb"


def test_predictions_are_labelled_as_predictions_and_the_evidence_level_is_a_fact():
    pkg = build_evidence(make_analysis(benchmark="failed"), "tradeoffs")
    labels = " ".join(f.label.lower() for f in pkg.facts)
    assert "predicted speedup" in labels and "evidence level" in labels
    assert any(f.value == "planner_validated" for f in pkg.facts)
    none = build_evidence(make_analysis(prediction=False), "tradeoffs")
    assert not any("predicted" in f.label.lower() for f in none.facts)


def test_kinds_select_different_subsets():
    a = make_analysis()
    by_kind = {k: {f.label for f in build_evidence(a, k).facts} for k in KINDS}
    assert any("slowest operator" in label.lower() for label in by_kind["plan"])
    assert not any("median" in label.lower() for label in by_kind["findings"])
    assert any("median" in label.lower() for label in by_kind["tradeoffs"])
    assert by_kind["pr_comment"] >= by_kind["findings"] | by_kind["tradeoffs"] | {l for l in by_kind["plan"]}


def test_findings_contribute_their_numeric_evidence_and_severity():
    pkg = build_evidence(make_analysis(), "findings")
    assert any("PG_SEQ_SCAN_FILTER_WASTE" in f.label and f.unit == "%" for f in pkg.facts)
    assert any(f.label.lower().startswith("critical findings") or f.label.lower().startswith("findings") for f in pkg.facts)


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError):
        build_evidence(make_analysis(), "poem")


def test_no_fact_contains_row_data_parameters_or_connection_strings():
    a = make_analysis()
    for kind in KINDS:
        blob = json.dumps([f.model_dump() for f in build_evidence(a, kind).facts])
        for secret in ("alice@example.com", "4242", "s3cret", "10.1.2.3", "postgresql://", "SELECT id FROM orders"):
            assert secret not in blob, f"{kind} leaks {secret}"


def test_numeric_values_lists_every_number_with_its_fact_id():
    pkg = build_evidence(make_analysis(), "tradeoffs")
    nv = pkg.numeric_values()
    assert nv and all(isinstance(v, (int, float)) for _id, v, _unit in nv)
    assert {fid for fid, _v, _u in nv} <= set(pkg.by_id())
    assert not any(isinstance(f.value, str) for f in pkg.facts if f.id in {fid for fid, *_ in nv})


def test_a_sparse_analysis_still_yields_a_package():
    pkg = build_evidence({"id": 9, "status": "completed", "plan": None, "findings": [], "candidates": [], "benchmarks": []}, "plan")
    assert isinstance(pkg, EvidencePackage)
