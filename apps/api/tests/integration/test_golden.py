"""Golden tests: for every curated slow query, the live stack must diagnose the expected problem and offer the expected
index, and the planner must agree it helps. These pin the product's core promise to real PostgreSQL behaviour."""

import httpx
import pytest
from querydoctor.lab.catalog import build_catalog
from stack import BASE, analyse

pytestmark = [pytest.mark.integration, pytest.mark.stack]

INDEX_SCANS = {"Index Scan", "Index Only Scan", "Bitmap Heap Scan"}
CATALOG = build_catalog("small")


@pytest.fixture(scope="module")
def http():
    with httpx.Client(base_url=BASE, timeout=60) as client:
        yield client


@pytest.fixture(scope="module")
def results(http):
    """Each catalog query analysed once, with execution, so measured-only rules can fire."""
    return {q.id: analyse(http, q.sql, q.params, execute=True) for q in CATALOG}


def candidate_columns(candidate: dict) -> list[str]:
    return [c["name"] for c in candidate["columns"]] or [candidate["expression"]]


@pytest.mark.parametrize("query", CATALOG, ids=lambda q: q.id)
def test_expected_findings_are_reported(results, query):
    analysis = results[query.id]
    assert analysis["status"] == "completed", analysis["error"]
    found = {f["rule_id"] for f in analysis["findings"]}
    assert set(query.expected_rule_ids) <= found, f"{query.id}: expected {query.expected_rule_ids}, got {sorted(found)}"
    for finding in analysis["findings"]:
        assert finding["evidence"] and finding["node_ids"] and finding["next_actions"], finding["rule_id"]


@pytest.mark.parametrize("query", [q for q in CATALOG if q.expected_candidate_table], ids=lambda q: q.id)
def test_expected_index_is_offered_and_planner_validated(results, query):
    candidates = [c for c in results[query.id]["candidates"] if not c["dropped"] and c["table_name"] == query.expected_candidate_table]
    matching = [c for c in candidates if candidate_columns(c) == query.expected_candidate_columns]
    assert matching, f"{query.id}: no candidate on {query.expected_candidate_columns}; got {[candidate_columns(c) for c in candidates]}"
    best = max(matching, key=lambda c: c["planner_score"] or 0)
    assert best["validation"] == "planner_validated", (query.id, best["ddl"], best["comparison"])
    assert best["comparison"]["uses_candidate"] is True


def test_headline_query_changes_from_a_sequential_scan_to_an_index_scan(results):
    q = next(q for q in CATALOG if q.id == "recent_orders_by_customer")
    top = min((c for c in results[q.id]["candidates"] if not c["dropped"]), key=lambda c: c["rank"])
    assert top["validation"] == "planner_validated"
    assert "Seq Scan" in top["comparison"]["removed_operators"]
    assert INDEX_SCANS & set(top["comparison"]["added_operators"])
    assert top["comparison"]["cost_reduction"] > 0.5


def test_nothing_is_ever_created_by_analysis(http, results):
    """Candidates are tested with HypoPG only: the target database must still have just its original indexes."""
    import psycopg

    with psycopg.connect("postgresql://querydoctor:querydoctor@localhost:5433/querydoctor_sample") as conn:
        names = sorted(r[0] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"))
    assert all(n.endswith("_pkey") or n == "idx_customers_email" for n in names), names
