import pytest

from querydoctor.benchmark import render_report, render_report_html

BENCH = {
    "id": 3, "candidate_id": 1, "status": "completed", "error": None,
    "result": {
        "before": {"n": 7, "median_ms": 742.0, "p95_ms": 811.0, "mean_ms": 750.0, "stdev_ms": 30.0, "cv": 0.04, "min_ms": 700.0, "max_ms": 820.0},
        "after": {"n": 7, "median_ms": 41.0, "p95_ms": 48.0, "mean_ms": 42.0, "stdev_ms": 3.0, "cv": 0.07, "min_ms": 39.0, "max_ms": 49.0},
        "improvement_pct": 94.47, "index_size_bytes": 86_000_000, "index_build_ms": 4200.0,
        "plan_fingerprint_before": "aaa", "plan_fingerprint_after": "bbb", "checksum_match": True,
        "warnings": ["Cache warming dominates: the first measured run was 3.4x the median."],
        "pg_version": "PostgreSQL 16.15", "settings": {"work_mem": "4096"}, "dataset_snapshot": "adf070622df0",
        "ddl": "CREATE INDEX ON orders (customer_id, created_at DESC)",
        "before_samples": [], "after_samples": [],
    },
}


def analysis(**overrides):
    base = {
        "id": 12, "status": "completed", "execute": True, "sql": "SELECT id FROM orders WHERE customer_id = $1",
        "params": [4242], "fingerprint": "f", "planning_time_ms": 0.2, "execution_time_ms": 742.1, "error": None,
        "plan": {
            "executed": True, "execution_time_ms": 742.1, "planning_time_ms": 0.2,
            "root": {"node_id": "0", "node_type": "Seq Scan", "relation_name": "orders", "exclusive_time_ms": 735.0,
                     "children": []},
        },
        "findings": [{
            "rule_id": "PG_SEQ_SCAN_FILTER_WASTE", "severity": "critical", "title": "Large scan discards most rows",
            "summary": "99.4% of visited rows were discarded.", "evidence": {"rows_visited": 1_824_403},
            "node_ids": ["0"], "confidence": 0.92, "next_actions": ["Evaluate an index on orders"],
        }],
        "candidates": [{
            "id": 1, "ddl": "CREATE INDEX ON orders (customer_id, created_at DESC)", "validation": "benchmark_validated",
            "planner_score": 0.97, "dropped": False, "drop_reason": None, "rank": 1, "rationale": ["equality on customer_id"],
            "comparison": {"removed_operators": ["Seq Scan"], "added_operators": ["Index Scan"], "cost_before": 61527.0, "cost_after": 2200.0},
        }],
        "benchmarks": [BENCH],
    }
    return {**base, **overrides}


def test_markdown_report_states_the_measured_evidence_in_the_guides_terms():
    md = render_report(analysis())
    for expected in ("Median", "742 ms", "41 ms", "811 ms", "48 ms", "94.5%", "82.0 MB", "4.2 s",
                     "Seq Scan → Index Scan", "Benchmark-validated", "adf070622df0", "PostgreSQL 16.15"):
        assert expected in md, expected
    assert "Cache warming dominates" in md
    assert "SELECT id FROM orders WHERE customer_id = $1" in md and "4242" in md
    assert "Large scan discards most rows" in md and "99.4% of visited rows were discarded." in md


def test_report_explains_the_methodology_and_never_leaks_connection_details_or_rows():
    md = render_report(analysis())
    assert "isolated copy" in md and "7 measured runs" in md
    assert "postgresql://" not in md and "password" not in md.lower()


def test_an_analysis_without_benchmarks_says_so_and_labels_candidates_honestly():
    bare = analysis(benchmarks=[])
    bare["candidates"][0]["validation"] = "planner_validated"
    md = render_report(bare)
    assert "No benchmark has been run" in md and "Planner-validated" in md
    assert "Benchmark-validated" not in md


def test_a_regression_is_reported_as_slower_not_hidden():
    slower = analysis()
    slower["benchmarks"][0] = {**BENCH, "result": {**BENCH["result"], "improvement_pct": -20.0}}
    assert "slower" in render_report(slower).lower()


def test_failed_and_planner_only_runs_render():
    failed = analysis(benchmarks=[{**BENCH, "status": "failed", "error": "The index could not be created", "result": None}])
    assert "The index could not be created" in render_report(failed)
    planner_only = analysis(execute=False, execution_time_ms=None)
    planner_only["plan"] = {**planner_only["plan"], "executed": False, "execution_time_ms": None}
    assert "estimates" in render_report(planner_only).lower()


def test_html_report_is_standalone_and_escapes_everything_user_supplied():
    nasty = analysis(sql="SELECT '<script>alert(1)</script>' FROM orders WHERE a = $1")
    nasty["findings"][0]["title"] = "<img src=x onerror=alert(1)>"
    html = render_report_html(nasty)
    assert html.startswith("<!doctype html>") and "<style>" in html
    assert "<script>alert(1)</script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html
    assert "742 ms" in html and "94.5%" in html
    assert "http://" not in html and "https://" not in html  # no external assets


@pytest.mark.parametrize("renderer", [render_report, render_report_html])
def test_renderers_tolerate_missing_optional_pieces(renderer):
    minimal = {"id": 1, "sql": "SELECT 1", "params": [], "status": "completed", "execute": False,
               "findings": [], "candidates": [], "benchmarks": [], "plan": None}
    assert renderer(minimal)
