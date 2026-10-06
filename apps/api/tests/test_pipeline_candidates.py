from querydoctor.candidates import IndexCandidate
from querydoctor.candidates.compare import PlanComparison
from querydoctor.candidates.hypopg import ScoredCandidate
from querydoctor.plan_parser import parse_explain
from stubs import raw_plan, strip_actuals
from test_pipeline import SQL, event_types, make_analysis

from app.services.candidates import run_candidate_stage

HEADLINE_SQL = "SELECT id, total_amount FROM orders WHERE customer_id = $1 AND created_at >= $2 ORDER BY created_at DESC"


def comparison(uses=True, score=0.9, cost_reduction=0.9, operator=0.7):
    return PlanComparison(
        cost_reduction=cost_reduction, rows_reduction=0.9, plan_risk_reduction=0.5, operator_improvement=operator,
        planner_score=score, uses_candidate=uses, removed_operators=["Seq Scan"] if uses else [],
        added_operators=["Index Scan"] if uses else [], cost_before=61_527.0, cost_after=2_200.0,
    )


def fake_scorer(outcomes: dict):
    """outcomes: ddl substring -> PlanComparison | str (error) ; anything else is ignored by the planner."""

    def scorer(conn, sql, params, original, candidates, table_rows=None):
        results = []
        for c in candidates:
            hit = next((v for k, v in outcomes.items() if k in c.ddl), comparison(uses=False, score=0.0, cost_reduction=0.0))
            if isinstance(hit, str):
                results.append(ScoredCandidate(c, error=hit))
            else:
                results.append(ScoredCandidate(c, hit, parse_explain(raw_plan("seqscan_filter_waste"))))
        return results

    return scorer


def run(db_session, stub, bus, outcomes, sql=HEADLINE_SQL, params=(4242, "2022-01-01")):
    analysis = make_analysis(db_session, sql=sql, params=params)
    deps = stub.deps(bus, scorer=fake_scorer(outcomes))
    from app.services.pipeline import run_analysis_pipeline

    run_analysis_pipeline(db_session, analysis.id, deps)
    db_session.refresh(analysis)
    return analysis


def test_candidates_that_change_the_plan_become_planner_validated(db_session, stub, bus):
    analysis = run(db_session, stub, bus, {"INCLUDE": comparison(score=0.95), "created_at DESC)": comparison(score=0.7)})
    assert analysis.status == "completed"
    kept = [c for c in analysis.candidates if not c.dropped]
    assert {c.validation for c in kept} == {"planner_validated"}
    top = kept[0]
    assert top.rank == 1 and top.planner_score == 0.95 and "INCLUDE" in top.ddl
    assert [c.rank for c in kept] == [1, 2]
    assert top.comparison["uses_candidate"] is True and top.comparison["removed_operators"] == ["Seq Scan"]
    assert top.table_name == "orders" and top.columns[0] == {"name": "customer_id", "desc": False}
    assert event_types(bus, analysis)[-2:] == ["testing_candidates", "completed"]


def test_candidates_the_planner_ignores_are_kept_as_suggested_with_a_note(db_session, stub, bus):
    analysis = run(db_session, stub, bus, {})
    assert analysis.candidates and all(c.validation == "suggested" for c in analysis.candidates)
    assert all(not c.dropped and c.planner_score == 0.0 for c in analysis.candidates)
    assert all("did not use" in c.rationale[-1] for c in analysis.candidates)


def test_small_improvements_are_dropped_with_a_reason(db_session, stub, bus):
    weak = comparison(score=0.15, cost_reduction=0.04, operator=0.0)  # uses the index, but barely cheaper
    analysis = run(db_session, stub, bus, {"INCLUDE": weak, "DESC)": comparison()})
    dropped = [c for c in analysis.candidates if c.dropped]
    assert len(dropped) == 1 and "INCLUDE" in dropped[0].ddl
    assert "below" in dropped[0].drop_reason and dropped[0].validation == "suggested"


def test_candidates_that_cannot_be_tested_are_dropped_with_the_error(db_session, stub, bus):
    analysis = run(db_session, stub, bus, {"INCLUDE": "could not be tested: boom", "DESC)": comparison()})
    broken = next(c for c in analysis.candidates if "INCLUDE" in c.ddl)
    assert broken.dropped and "boom" in broken.drop_reason


def test_redundant_candidates_are_stored_as_dropped_with_the_pruning_reason(db_session, stub, bus):
    # the stub schema already has orders_pkey (id); a query that only filters on id yields a duplicate of it
    analysis = run(db_session, stub, bus, {}, sql="SELECT total_amount FROM orders WHERE id = $1", params=(1,))
    assert analysis.candidates and all(c.dropped for c in analysis.candidates)
    assert any("existing index orders_pkey" in c.drop_reason for c in analysis.candidates)


def test_a_candidate_stage_crash_does_not_fail_the_analysis(db_session, stub, bus):
    def exploding(*a, **k):
        raise RuntimeError("hypopg missing")

    analysis = make_analysis(db_session, sql=HEADLINE_SQL, params=(4242, "2022-01-01"))
    from app.services.pipeline import run_analysis_pipeline

    run_analysis_pipeline(db_session, analysis.id, stub.deps(bus, scorer=exploding))
    db_session.refresh(analysis)
    assert analysis.status == "completed"  # diagnosis still delivered; candidates are an optional extra
    assert analysis.candidates == []


def test_planner_only_and_executed_analyses_both_test_candidates(db_session, stub, bus):
    for execute in (False, True):
        analysis = make_analysis(db_session, execute=execute, sql=HEADLINE_SQL, params=(4242, "2022-01-01"))
        from app.services.pipeline import run_analysis_pipeline

        run_analysis_pipeline(db_session, analysis.id, stub.deps(bus, scorer=fake_scorer({"INCLUDE": comparison()})))
        db_session.refresh(analysis)
        assert any(c.validation == "planner_validated" for c in analysis.candidates)


def test_original_plan_for_scoring_is_always_the_planner_only_plan(db_session, stub, bus):
    """HypoPG only influences plain EXPLAIN, so the baseline must be planner-only even for executed analyses."""
    seen = {}

    def scorer(conn, sql, params, original, candidates, table_rows=None):
        seen["executed"] = original.executed
        return [ScoredCandidate(c, comparison(uses=False)) for c in candidates]

    stub.planner_only_raw = strip_actuals(stub.raw)
    analysis = make_analysis(db_session, execute=True, sql=HEADLINE_SQL, params=(4242, "2022-01-01"))
    from app.services.pipeline import run_analysis_pipeline

    run_analysis_pipeline(db_session, analysis.id, stub.deps(bus, scorer=scorer))
    assert seen["executed"] is False
    assert [c["execute"] for c in stub.explain_calls] == [True, False]  # the measured run, then a planner-only one


def test_run_candidate_stage_is_importable_for_reuse():
    assert callable(run_candidate_stage) and IndexCandidate and SQL
