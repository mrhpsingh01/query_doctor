"""The workload dashboard against the real stack and the lab's demo traffic."""

import time

import httpx
import psycopg
import pytest
from labdsn import TARGET_ADMIN_DSN
from stack import BASE, project_id

from querydoctor.lab.catalog import build_catalog
from querydoctor.safety import validate_query

pytestmark = [pytest.mark.integration, pytest.mark.stack]

CATALOG = {q.id: q for q in build_catalog("small")}
CUSTOMER_ORDERS = validate_query(CATALOG["recent_orders_by_customer"].sql).fingerprint


def calls_of(conn, query_id: str) -> int:
    """Calls recorded in pg_stat_statements for one catalog query (its normalized text starts with the catalog SQL)."""
    return conn.execute(
        "SELECT coalesce(sum(calls), 0) FROM pg_stat_statements WHERE query LIKE %s", (CATALOG[query_id].sql + "%",)
    ).fetchone()[0]


@pytest.fixture(scope="module")
def http():
    with httpx.Client(base_url=BASE, timeout=120) as client:
        yield client


def _demo(http, pid, mode, expect_calls):
    """Start a demo run through the API and wait until its traffic shows up in pg_stat_statements."""
    assert http.post(f"/api/projects/{pid}/workload/demo", json={"mode": mode}).status_code == 202
    deadline = time.monotonic() + 240
    with psycopg.connect(TARGET_ADMIN_DSN, autocommit=True) as conn:
        while time.monotonic() < deadline:
            if calls_of(conn, "recent_orders_by_customer") >= expect_calls:
                break
            time.sleep(2)
        else:
            pytest.fail(f"the {mode} demo traffic did not appear in time")


@pytest.fixture(scope="module")
def baseline(http):
    pid = project_id(http)
    with psycopg.connect(TARGET_ADMIN_DSN, autocommit=True) as conn:
        conn.execute("SELECT pg_stat_statements_reset()")
    _demo(http, pid, "baseline", 1000)
    time.sleep(5)  # the run keeps going after the hot query's first calls: let it finish
    first = http.post(f"/api/projects/{pid}/workload/import")
    assert first.status_code == 201, first.text
    return pid, first.json()


def test_the_frequent_query_is_ranked_first_by_total_time_while_another_is_slowest_per_call(http, baseline):
    pid, snapshot = baseline
    body = http.get(f"/api/projects/{pid}/workload").json()
    assert snapshot["query_count"] >= 8 and len(body["queries"]) >= 8
    first = body["queries"][0]
    slowest = max(body["queries"], key=lambda q: q["mean_ms"])
    assert first["calls"] >= 1000  # called often ...
    assert slowest["fingerprint"] != first["fingerprint"] and slowest["mean_ms"] > first["mean_ms"]  # ... though not slowest per call
    assert slowest["calls"] < first["calls"] and body["panels"]["slowest_p95"][0]["fingerprint"] == slowest["fingerprint"]
    assert all("insufficient privilege" not in q["sample_sql"] for q in body["queries"])


def test_missing_validation_lists_the_queries_without_benchmark_evidence(http, baseline):
    pid, _ = baseline
    body = http.get(f"/api/projects/{pid}/workload").json()
    validated = {
        h["fingerprint"] for h in http.get(f"/api/projects/{pid}/fingerprints").json() if h["best_validation"] == "benchmark_validated"
    }
    expected = [q["fingerprint"] for q in body["queries"] if q["fingerprint"] not in validated][:10]  # by total time
    assert [q["fingerprint"] for q in body["panels"]["missing_validation"]] == expected
    assert not validated & {q["fingerprint"] for q in body["panels"]["missing_validation"]}  # a benchmarked query never lists as missing evidence
    assert expected  # the demo traffic always contains shapes nobody has benchmarked yet


def test_the_regress_demo_followed_by_a_second_import_reports_a_regression(http, baseline):
    pid, first = baseline
    with psycopg.connect(TARGET_ADMIN_DSN, autocommit=True) as conn:
        sort_calls_before = calls_of(conn, "unnecessary_large_sort")
    assert http.post(f"/api/projects/{pid}/workload/demo", json={"mode": "regress"}).status_code == 202
    deadline = time.monotonic() + 240
    with psycopg.connect(TARGET_ADMIN_DSN, autocommit=True) as conn:
        while time.monotonic() < deadline:
            if calls_of(conn, "unnecessary_large_sort") >= sort_calls_before + 25:
                break
            time.sleep(2)
        else:
            pytest.fail("the regress demo traffic did not appear in time")
    assert http.post(f"/api/projects/{pid}/workload/import").status_code == 201
    body = http.get(f"/api/projects/{pid}/workload").json()
    assert body["compare_to"]["id"] == first["id"]
    assert body["regressions"] and body["regressions"][0]["ratio"] >= 1.5
    assert body["panels"]["largest_regression"][0]["fingerprint"] == body["regressions"][0]["fingerprint"]
