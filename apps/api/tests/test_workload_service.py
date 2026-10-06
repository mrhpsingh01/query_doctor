from datetime import UTC, datetime, timedelta

import pytest
from querydoctor.lab.catalog import build_catalog
from querydoctor.safety import validate_query
from querydoctor.workload import StatementRow

from app.models import Analysis, Project, QuerySnapshot, RecommendationCandidate, WorkloadQueryRow, WorkloadSnapshot
from app.services import workload as svc

ORDERS = "SELECT id, total_amount FROM orders WHERE customer_id = $1"
USERS = "SELECT id FROM customers WHERE email = $1"
CATALOG = {q.id: q for q in build_catalog("small")}


def stmt(query, calls, mean, std=0.0, **kw):
    return StatementRow(
        query=query, calls=calls, total_ms=calls * mean, mean_ms=mean, stddev_ms=std, min_ms=mean / 2, max_ms=mean * 2,
        rows=calls, shared_hit_blocks=kw.get("hit", 10), shared_read_blocks=kw.get("read", 0), temp_blocks=kw.get("temp", 0),
    )


@pytest.fixture
def project(db_session):
    p = Project(name="p")
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture
def pgss(monkeypatch):
    """The statements the next import will read (the stub replaces the pg_stat_statements reader)."""
    rows: list[StatementRow] = []
    monkeypatch.setattr(svc, "read_statements", lambda conn, limit=500: rows[:limit])
    return rows


def add_analysis(session, project, sql, *, plan_fp=None, ms=None, validation=None, params=(), created=None):
    vq = validate_query(sql)
    snap = QuerySnapshot(project_id=project.id, sql=vq.sql, normalized_sql=vq.normalized_sql, fingerprint=vq.fingerprint, params=list(params))
    a = Analysis(
        project_id=project.id, snapshot=snap, status="completed", plan_fingerprint=plan_fp, execution_time_ms=ms,
        execute=ms is not None, created_at=created or datetime.now(UTC),
    )
    if validation:
        a.candidates.append(RecommendationCandidate(candidate_key="k", table_name="orders", ddl="d", validation=validation))
    session.add(a)
    session.commit()
    return a


def test_import_persists_the_snapshot_merged_queries_and_the_skipped_count(db_session, project, pgss):
    pgss += [stmt(ORDERS, 100, 5.0), stmt(USERS, 10, 1.0), stmt("DELETE FROM orders WHERE id = $1", 3, 1.0), stmt("SELECT 1", 4, 0.1)]
    snap = svc.import_snapshot(db_session, object(), project.id)
    assert (snap.query_count, snap.skipped_count, snap.engine, snap.source) == (2, 2, "postgres", "pg_stat_statements")
    rows = db_session.query(WorkloadQueryRow).filter_by(snapshot_id=snap.id).order_by(WorkloadQueryRow.total_ms.desc()).all()
    assert [r.calls for r in rows] == [100, 10]
    assert rows[0].fingerprint == validate_query(ORDERS).fingerprint
    assert rows[0].mean_ms == 5.0 and rows[0].p95_est_ms == 5.0 and rows[0].sample_sql == ORDERS and rows[0].tables == ["orders"]


def test_an_empty_pg_stat_statements_still_records_an_empty_snapshot(db_session, project, pgss):
    snap = svc.import_snapshot(db_session, object(), project.id)
    assert (snap.query_count, snap.skipped_count) == (0, 0)
    assert svc.dashboard(db_session, project.id)["queries"] == []


def test_dashboard_with_no_snapshot_is_empty(db_session, project):
    data = svc.dashboard(db_session, project.id)
    assert data["snapshot"] is None and data["queries"] == [] and data["regressions"] == []
    assert all(v == [] for v in data["panels"].values())


def test_the_limit_is_passed_to_the_reader(db_session, project, pgss):
    pgss += [stmt(ORDERS, 5, 1.0), stmt(USERS, 5, 1.0)]
    assert svc.import_snapshot(db_session, object(), project.id, limit=1).query_count == 1


def test_dashboard_ranks_by_total_time_and_marks_missing_validation(db_session, project, pgss):
    pgss += [stmt(ORDERS, 10_000, 5.0), stmt(USERS, 1, 5000.0)]  # a hot 5 ms query beats one 5 s call
    snap = svc.import_snapshot(db_session, object(), project.id)
    add_analysis(db_session, project, ORDERS, validation="benchmark_validated")
    add_analysis(db_session, project, USERS, validation="planner_validated")  # only planner-validated: still missing
    data = svc.dashboard(db_session, project.id)
    assert data["snapshot"]["id"] == snap.id and data["compare_to"] is None
    assert [q["rank"] for q in data["queries"]] == [1, 2] and data["queries"][0]["calls"] == 10_000
    assert abs(sum(q["share"] for q in data["queries"]) - 1.0) < 1e-9
    assert [q["fingerprint"] for q in data["panels"]["missing_validation"]] == [validate_query(USERS).fingerprint]
    assert data["panels"]["slowest_p95"][0]["calls"] == 1 and set(data["panels"]) >= {"highest_total_time", "most_unstable"}


def test_the_ranked_list_is_capped_at_100(db_session, project, pgss):
    pgss += [stmt(f"SELECT id FROM t{i} WHERE a = $1", 5, 1.0 + i) for i in range(120)]
    svc.import_snapshot(db_session, object(), project.id)
    assert len(svc.dashboard(db_session, project.id)["queries"]) == 100


def test_compare_defaults_to_the_previous_snapshot_and_finds_the_regression(db_session, project, pgss):
    pgss[:] = [stmt(ORDERS, 100, 5.0)]
    first = svc.import_snapshot(db_session, object(), project.id)
    slow = stmt(ORDERS, 160, 0.0)  # 100 calls at 5 ms, then 60 more at 25 ms: five times slower
    slow.total_ms = 100 * 5.0 + 60 * 25.0
    slow.mean_ms = slow.total_ms / 160
    pgss[:] = [slow]
    second = svc.import_snapshot(db_session, object(), project.id)
    assert svc.latest_two(db_session, project.id) == (second, first)
    data = svc.dashboard(db_session, project.id)
    assert data["compare_to"]["id"] == first.id and [r["fingerprint"] for r in data["regressions"]] == [validate_query(ORDERS).fingerprint]
    assert data["regressions"][0]["ratio"] == pytest.approx(5.0)
    assert data["panels"]["largest_regression"][0]["ratio"] == pytest.approx(5.0)
    # looking at the older snapshot has nothing before it to compare with
    assert svc.dashboard(db_session, project.id, snapshot_id=first.id)["regressions"] == []
    # an explicit compare id is honoured
    assert svc.dashboard(db_session, project.id, snapshot_id=second.id, compare_id=first.id)["compare_to"]["id"] == first.id


def test_latest_two_with_fewer_than_two_snapshots(db_session, project, pgss):
    assert svc.latest_two(db_session, project.id) == (None, None)
    only = svc.import_snapshot(db_session, object(), project.id)
    assert svc.latest_two(db_session, project.id) == (only, None)


def test_dashboard_for_another_projects_snapshot_is_not_found(db_session, project, pgss):
    other = Project(name="other")
    db_session.add(other)
    db_session.commit()
    snap = svc.import_snapshot(db_session, object(), other.id)
    with pytest.raises(LookupError):
        svc.dashboard(db_session, project.id, snapshot_id=snap.id)


def test_fingerprint_history_groups_analyses_with_measured_latency_and_plan_changes(db_session, project):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    for i, (fp, ms, validation) in enumerate(
        [("planA", 100.0, None), ("planA", 120.0, "planner_validated"), ("planB", 300.0, "benchmark_validated"), ("planB", None, None)]
    ):
        add_analysis(db_session, project, ORDERS, plan_fp=fp, ms=ms, validation=validation, created=t0 + timedelta(days=i))
    add_analysis(db_session, project, USERS, plan_fp="planX", ms=7.0, created=t0)
    history = {h["fingerprint"]: h for h in svc.fingerprint_history(db_session, project.id)}
    h = history[validate_query(ORDERS).fingerprint]
    assert h["analysis_count"] == 4 and h["executed_count"] == 3
    assert h["first_seen"].startswith("2026-01-01") and h["last_seen"].startswith("2026-01-04")
    assert h["mean_execution_ms"] == pytest.approx((100 + 120 + 300) / 3)
    assert 120.0 < h["p95_execution_ms"] <= 300.0
    assert h["plan_change_count"] == 1 and h["best_validation"] == "benchmark_validated"
    assert h["normalized_sql"]
    only = history[validate_query(USERS).fingerprint]
    assert only["plan_change_count"] == 0 and only["best_validation"] is None and only["p95_execution_ms"] == 7.0


def test_a_fingerprint_with_no_executed_analyses_has_no_latency(db_session, project):
    add_analysis(db_session, project, ORDERS, plan_fp="planA")
    h = svc.fingerprint_history(db_session, project.id)[0]
    assert h["mean_execution_ms"] is None and h["p95_execution_ms"] is None and h["executed_count"] == 0


def test_query_detail_has_trend_analyses_plan_changes_and_catalog_sample(db_session, project, pgss):
    sql = CATALOG["recent_orders_by_customer"].sql
    fingerprint = validate_query(sql).fingerprint
    pgss[:] = [stmt(sql, 100, 5.0)]
    svc.import_snapshot(db_session, object(), project.id)
    pgss[:] = [stmt(sql, 200, 6.0)]
    svc.import_snapshot(db_session, object(), project.id)
    add_analysis(db_session, project, sql, plan_fp="a", ms=10.0, params=[1, "2022-01-01"])
    add_analysis(db_session, project, sql, plan_fp="b", ms=4.0, params=[1, "2022-01-01"])
    detail = svc.query_detail(db_session, project.id, fingerprint)
    assert [t["calls"] for t in detail["trend"]] == [100, 200]
    assert [a["plan_fingerprint"] for a in detail["analyses"]] == ["a", "b"]
    assert len(detail["plan_changes"]) == 1 and detail["plan_changes"][0]["before_fingerprint"] == "a"
    assert detail["sample"]["query_id"] == "recent_orders_by_customer" and detail["sample"]["params"] == CATALOG["recent_orders_by_customer"].params
    assert detail["fingerprint"] == fingerprint and detail["sample_sql"]


def test_query_detail_of_a_non_catalog_query_has_no_sample_and_unknown_is_none(db_session, project, pgss):
    pgss[:] = [stmt(ORDERS, 10, 1.0)]
    svc.import_snapshot(db_session, object(), project.id)
    assert svc.query_detail(db_session, project.id, validate_query(ORDERS).fingerprint)["sample"] is None
    assert svc.query_detail(db_session, project.id, "0" * 16) is None
