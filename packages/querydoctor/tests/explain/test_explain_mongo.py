"""Explanations of a MongoDB analysis state MongoDB facts: no PostgreSQL cost, estimate or sequential-scan wording."""

import json
import pathlib

import pytest

from querydoctor.explain import build_evidence
from querydoctor.explain.ollama import SYSTEM_PROMPT
from querydoctor.explain.template import explain_template
from querydoctor.mongo.plan import normalize_plan

FIXTURES = pathlib.Path(__file__).resolve().parents[4] / "fixtures" / "mongo_plans"
KINDS = ["plan", "findings", "tradeoffs", "next_experiment", "pr_comment"]


def analysis() -> dict:
    raw = json.loads((FIXTURES / "mongo_collscan_by_customer.stats.json").read_text(encoding="utf-8"))
    plan = normalize_plan(raw).model_dump()
    return {
        "id": 1, "status": "completed", "execute": True, "engine": "mongodb", "sql": '{"find": "orders"}', "params": [], "plan": plan,
        "execution_time_ms": plan["execution_time_ms"], "findings": [], "candidates": [], "benchmarks": [],
    }


@pytest.mark.parametrize("kind", KINDS)
def test_facts_and_template_never_mention_postgresql_estimates_or_sequential_scans(kind):
    pkg = build_evidence(analysis(), kind)
    text = " ".join(f.text for f in pkg.facts) + " " + explain_template(pkg).text
    for forbidden in ("PostgreSQL", "plan cost", "sequential scan", "expects about"):
        assert forbidden not in text, (forbidden, text)


def test_the_plan_explanation_counts_collection_scans_and_says_what_they_do():
    pkg = build_evidence(analysis(), "plan")
    scans = [f for f in pkg.facts if f.label == "Collection scans in the plan"]
    assert len(scans) == 1 and scans[0].value == 1
    text = explain_template(pkg).text
    assert "collection scan" in text and "every document" in text


def test_the_model_prompt_is_not_postgresql_specific():
    assert "PostgreSQL performance analysis" not in SYSTEM_PROMPT
    assert "MongoDB" in SYSTEM_PROMPT
