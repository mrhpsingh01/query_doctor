"""A crashed worker leaves jobs 'running' forever (Celery redelivery returns early for non-queued jobs). The janitor fails
jobs that have not finished within a generous window so the UI reports a clear result instead of waiting forever."""

from datetime import UTC, datetime, timedelta

from app.models import Analysis, BenchmarkRun, Project, QuerySnapshot, RecommendationCandidate
from app.workers.janitor import fail_stale_jobs

NOW = datetime.now(UTC)


def seed(session):
    project = Project(name="p")
    session.add(project)
    session.flush()
    snapshot = QuerySnapshot(project_id=project.id, sql="SELECT 1", normalized_sql="SELECT ?", fingerprint="f", params=[])
    session.add(snapshot)
    session.flush()
    analysis = Analysis(project_id=project.id, snapshot=snapshot, status="completed")
    session.add(analysis)
    session.flush()
    candidate = RecommendationCandidate(analysis_id=analysis.id, candidate_key="k", table_name="t", ddl="CREATE INDEX ON t (a)")
    session.add(candidate)
    session.flush()
    return project, snapshot, analysis, candidate


def run(session, analysis, candidate, **kw):
    r = BenchmarkRun(analysis_id=analysis.id, candidate_id=candidate.id, config={}, **kw)
    session.add(r)
    session.flush()
    return r


def test_old_running_and_queued_benchmarks_are_failed_with_a_clear_message(db_session):
    _p, _s, analysis, candidate = seed(db_session)
    stuck_running = run(db_session, analysis, candidate, status="running", started_at=NOW - timedelta(hours=1), created_at=NOW - timedelta(hours=1))
    db_session.commit()
    cand2 = RecommendationCandidate(analysis_id=analysis.id, candidate_key="k2", table_name="t", ddl="CREATE INDEX ON t (b)")
    db_session.add(cand2)
    db_session.flush()
    never_started = run(db_session, analysis, cand2, status="queued", created_at=NOW - timedelta(hours=1))
    db_session.commit()

    assert fail_stale_jobs(_factory(db_session), max_age_s=900) == 2
    db_session.expire_all()
    for r in (stuck_running, never_started):
        refreshed = db_session.get(BenchmarkRun, r.id)
        assert refreshed.status == "failed" and "worker" in refreshed.error and refreshed.finished_at is not None


def test_recent_and_finished_jobs_are_left_alone(db_session):
    _p, _s, analysis, candidate = seed(db_session)
    fresh = run(db_session, analysis, candidate, status="running", started_at=NOW - timedelta(seconds=30), created_at=NOW)
    done = run(db_session, analysis, candidate, status="completed", started_at=NOW - timedelta(hours=2), created_at=NOW - timedelta(hours=2))
    db_session.commit()
    assert fail_stale_jobs(_factory(db_session), max_age_s=900) == 0
    db_session.expire_all()
    assert db_session.get(BenchmarkRun, fresh.id).status == "running"
    assert db_session.get(BenchmarkRun, done.id).status == "completed"


def test_stale_analyses_are_failed_too(db_session):
    project, snapshot, _a, _c = seed(db_session)
    stuck = Analysis(project_id=project.id, snapshot=snapshot, status="running", started_at=NOW - timedelta(hours=1), created_at=NOW - timedelta(hours=1))
    db_session.add(stuck)
    db_session.commit()
    assert fail_stale_jobs(_factory(db_session), max_age_s=900) == 1
    db_session.expire_all()
    assert db_session.get(Analysis, stuck.id).status == "failed"


def _factory(session):
    """A sessionmaker-like callable that hands out the test session (usable as a context manager)."""

    class Ctx:
        def __enter__(self_inner):
            return session

        def __exit__(self_inner, *exc):
            return False

    return lambda: Ctx()
