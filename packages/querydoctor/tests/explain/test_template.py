import pytest
from test_facts import KINDS, make_analysis

from querydoctor.explain import Explanation, build_evidence, explain_template
from querydoctor.explain.validator import validate_claims

VARIANTS = [{}, {"executed": False}, {"benchmark": None}, {"benchmark": "failed"}, {"prediction": False}]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("variant", VARIANTS)
def test_every_sentence_cites_existing_facts_and_the_whole_text_passes_the_validator(kind, variant):
    pkg = build_evidence(make_analysis(**variant), kind)
    exp = explain_template(pkg)
    assert isinstance(exp, Explanation) and exp.source == "template" and exp.fallback_reason is None and exp.dropped == []
    assert exp.sentences, "a template explanation is never empty when there are facts"
    for s in exp.sentences:
        assert s.fact_ids and set(s.fact_ids) <= set(pkg.by_id()), s
        assert all(f"[{', '.join(s.fact_ids)}]" in exp.text for _ in [0])
    result = validate_claims(exp.text, pkg)
    assert result.ok and result.dropped == [], result


def test_the_explanation_is_deterministic():
    pkg = build_evidence(make_analysis(), "pr_comment")
    assert explain_template(pkg) == explain_template(build_evidence(make_analysis(), "pr_comment"))


def test_a_plan_explanation_names_the_time_and_the_slowest_step():
    text = explain_template(build_evidence(make_analysis(), "plan")).text
    assert "742" not in text or "ms" in text
    assert "slowest" in text.lower() and "ms" in text


def test_a_planner_only_plan_explanation_makes_no_timing_claims():
    text = explain_template(build_evidence(make_analysis(executed=False), "plan")).text
    assert "estimates" in text.lower() and " ms" not in text


def test_a_tradeoff_explanation_states_the_measured_benefit_and_the_cost():
    text = explain_template(build_evidence(make_analysis(), "tradeoffs")).text
    assert "95.9%" in text and "5 MB" in text and "68 ms" in text
    assert "benchmark" in text.lower()


def test_without_a_benchmark_a_tradeoff_explanation_says_the_prediction_is_not_a_measurement():
    text = explain_template(build_evidence(make_analysis(benchmark=None), "tradeoffs")).text
    assert "not a measurement" in text.lower() or "prediction" in text.lower()
    assert "95.9%" not in text and "benchmark" not in text.lower().replace("not been benchmarked", "")


def test_the_next_experiment_depends_on_how_far_the_evidence_has_got():
    unmeasured = explain_template(build_evidence(make_analysis(benchmark=None), "next_experiment")).text
    assert "benchmark" in unmeasured.lower() and "planner-validated" in unmeasured.lower()
    measured = explain_template(build_evidence(make_analysis(), "next_experiment")).text
    assert "benchmark-validated" in measured.lower()


def test_a_pr_comment_is_short_markdown_with_the_evidence_level():
    text = explain_template(build_evidence(make_analysis(), "pr_comment")).text
    assert text.startswith("**QueryDoctor**")
    assert "benchmark-validated" in text.lower() and "95.9%" in text


def test_findings_are_explained_one_by_one():
    exp = explain_template(build_evidence(make_analysis(), "findings"))
    assert any("PG_SEQ_SCAN" in f for s in exp.sentences for f in s.fact_ids) or len(exp.sentences) >= 2
    assert "critical" in exp.text.lower()


def test_an_analysis_with_nothing_to_say_gets_an_honest_one_liner():
    exp = explain_template(build_evidence({"id": 9, "status": "completed", "plan": None, "findings": [], "candidates": [], "benchmarks": []}, "plan"))
    assert exp.sentences == [] and "not enough evidence" in exp.text.lower()
