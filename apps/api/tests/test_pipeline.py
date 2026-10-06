import logging

import pytest
from querydoctor.db import ExplainFailed, ExplainTimeout
from querydoctor.safety import validate_query

from app.models import Analysis, Project, QuerySnapshot
from app.services.events import InMemoryEventBus
from app.services.pipeline import run_analysis_pipeline

SQL = "SELECT id, total_amount FROM orders WHERE customer_id = $1"


def make_analysis(session, *, execute=False, params=(4242,), sql=SQL):
    project = Project(name="p")
    session.add(project)
    session.flush()
    vq = validate_query(sql)
    snap = QuerySnapshot(
        project_id=project.id, sql=vq.sql, normalized_sql=vq.normalized_sql, fingerprint=vq.fingerprint, params=list(params)
    )
    session.add(snap)
    session.flush()
    analysis = Analysis(project_id=project.id, snapshot_id=snap.id, status="queued", execute=execute)
    session.add(analysis)
    session.commit()
    return analysis


def event_types(bus, analysis):
    return [e["type"] for _i, e in bus.read(f"analysis:{analysis.id}", None, 0)]


def test_happy_path_persists_plan_findings_and_emits_ordered_events(db_session, stub, bus):
    analysis = make_analysis(db_session)
    pids_during_run = []
    stub.on_schema = lambda: pids_during_run.append(analysis.backend_pid)
    run_analysis_pipeline(db_session, analysis.id, stub.deps(bus))
    db_session.refresh(analysis)
    assert analysis.status == "completed" and analysis.error is None
    assert analysis.plan_json is not None and analysis.plan_fingerprint
    assert analysis.execution_time_ms == pytest.approx(742.1)
    assert pids_during_run == [4242]  # tracked while running so the API can cancel the statement ...
    assert analysis.backend_pid is None  # ... and cleared afterwards so a recycled pid is never cancelled
    assert analysis.schema_json["tables"]["orders"]["row_estimate"] == 1_824_403
    assert [f.rule_id for f in analysis.findings] == ["PG_SEQ_SCAN_FILTER_WASTE"]
    assert event_types(bus, analysis) == [
        "validating_query", "collecting_schema", "generating_plan", "diagnosing", "testing_candidates", "completed",
    ]
    assert stub.explain_calls == [{"sql": analysis.snapshot.sql, "params": [4242], "execute": False}]


def test_execute_flag_reaches_the_explain_runner(db_session, stub, bus):
    analysis = make_analysis(db_session, execute=True)
    run_analysis_pipeline(db_session, analysis.id, stub.deps(bus))
    assert stub.explain_calls[0]["execute"] is True


@pytest.mark.parametrize(
    "error,expected",
    [(ExplainFailed('relation "x" does not exist'), 'relation "x" does not exist'), (ExplainTimeout("t"), "timed out")],
)
def test_target_errors_fail_the_analysis_with_a_message(db_session, stub, bus, error, expected):
    stub.explain_error = error
    analysis = make_analysis(db_session)
    run_analysis_pipeline(db_session, analysis.id, stub.deps(bus))
    db_session.refresh(analysis)
    assert analysis.status == "failed"
    assert expected in analysis.error
    assert event_types(bus, analysis)[-1] == "failed"


def test_unexpected_crash_fails_cleanly_without_leaking_details(db_session, stub, bus, caplog):
    stub.explain_error = RuntimeError("secret internal detail")
    analysis = make_analysis(db_session)
    with caplog.at_level(logging.ERROR):
        run_analysis_pipeline(db_session, analysis.id, stub.deps(bus))
    db_session.refresh(analysis)
    assert analysis.status == "failed"
    assert "secret internal detail" not in analysis.error
    assert "internal error" in analysis.error
    assert "secret internal detail" in caplog.text


def test_cancel_requested_before_start(db_session, stub, bus):
    analysis = make_analysis(db_session)
    analysis.cancel_requested = True
    db_session.commit()
    run_analysis_pipeline(db_session, analysis.id, stub.deps(bus))
    db_session.refresh(analysis)
    assert analysis.status == "cancelled"
    assert stub.explain_calls == []
    assert event_types(bus, analysis)[-1] == "cancelled"


def test_cancel_between_stages_stops_before_explain(db_session, stub, bus):
    analysis = make_analysis(db_session)

    def request_cancel():
        analysis.cancel_requested = True
        db_session.commit()

    stub.on_schema = request_cancel
    run_analysis_pipeline(db_session, analysis.id, stub.deps(bus))
    db_session.refresh(analysis)
    assert analysis.status == "cancelled"
    assert stub.explain_calls == []


def test_cancelled_statement_is_reported_as_cancelled_not_failed(db_session, stub, bus):
    analysis = make_analysis(db_session)

    def cancel_then_timeout(*a, **k):
        analysis.cancel_requested = True
        db_session.commit()
        raise ExplainTimeout("canceled")

    stub.explain = lambda conn, sql, params, *, execute: cancel_then_timeout()
    run_analysis_pipeline(db_session, analysis.id, stub.deps(bus))
    db_session.refresh(analysis)
    assert analysis.status == "cancelled"


def test_oversized_plan_is_rejected(db_session, stub, bus):
    analysis = make_analysis(db_session)
    run_analysis_pipeline(db_session, analysis.id, stub.deps(bus, max_plan_bytes=100))
    db_session.refresh(analysis)
    assert analysis.status == "failed" and "too large" in analysis.error
    assert analysis.plan_json is None


def test_garbage_plan_fails_cleanly(db_session, stub, bus):
    stub.raw = [{"not": "a plan"}]
    analysis = make_analysis(db_session)
    run_analysis_pipeline(db_session, analysis.id, stub.deps(bus))
    db_session.refresh(analysis)
    assert analysis.status == "failed"


def test_a_dead_event_bus_never_breaks_the_analysis(db_session, stub):
    class BrokenBus(InMemoryEventBus):
        def publish(self, key, event):
            raise ConnectionError("redis is down")

    analysis = make_analysis(db_session)
    run_analysis_pipeline(db_session, analysis.id, stub.deps(BrokenBus()))
    db_session.refresh(analysis)
    assert analysis.status == "completed"


def test_already_finished_analysis_is_not_rerun(db_session, stub, bus):
    analysis = make_analysis(db_session)
    analysis.status = "completed"
    db_session.commit()
    run_analysis_pipeline(db_session, analysis.id, stub.deps(bus))
    assert stub.explain_calls == []
