"""Gaps found by the final whole-branch review: unit words, spelled-out and prefixed numbers, evidence-level and safety
claims, and database commands that are not SQL DDL."""

import pytest
from test_claim_validator import FACTS, f

from querydoctor.explain.validator import validate_claims

LEVEL_FACTS = [
    *FACTS,
    f(11, "Index idea 1 evidence level", "suggested"),
    f(12, "Index idea 2 DDL", 'db.orders.createIndex({"customer_id": 1})'),
]


def dropped(sentence, facts=FACTS):
    # a second supported sentence keeps the draft from being rejected outright, so the drop itself is observable
    result = validate_claims(f"{sentence} It scanned 1,824,403 rows.", facts)
    return sentence in result.dropped


@pytest.mark.parametrize(
    "sentence",
    [
        "It cuts the latency by 6 seconds.",  # 6 is not a time in the evidence (742 ms - 30 ms = 0.712 s)
        "It cuts the latency by 7 seconds.",  # 7 is a count (runs), never a time
        "The query is about 40 times faster.",
        "That makes it 3 times slower.",
        "The index made it 93 percent faster.",
        "It is 50 percent faster.",
        "The index made the query ten times faster.",
        "It is twice as fast.",
        "That is a tenfold improvement.",
        "It halves the time.",
        "Expect to save about x40.",
        "It takes .5 seconds.",
        "That is 5 minutes of work saved.",
    ],
)
def test_claims_the_evidence_does_not_support_are_dropped_in_any_wording(sentence):
    assert dropped(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "The index made it 94.5 percent faster.",
        "That is roughly 8 times faster.",
        "The median fell by about 0.7 seconds.",
        "Each side was measured 7 times.",  # a count, not a factor
    ],
)
def test_the_same_wordings_are_kept_when_the_evidence_supports_them(sentence):
    assert not dropped(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "Index idea 1 has been benchmark-validated and measured to be safe. [F11]",
        "Index idea 1 is planner validated. [F11]",
        "It is guaranteed to help. [F2]",
        "The index is safe in production. [F2]",
        "This is a risk-free change. [F2]",
    ],
)
def test_evidence_level_and_safety_claims_must_come_from_the_cited_facts(sentence):
    assert dropped(sentence, LEVEL_FACTS)


def test_an_evidence_level_the_cited_fact_states_is_kept():
    result = validate_claims("Index idea 1 is suggested, which means it is not tested yet. [F11] It scanned 1,824,403 rows. [F3]", LEVEL_FACTS)
    assert result.ok and not result.dropped
    ok = [*LEVEL_FACTS, f(13, "Index idea 2 evidence level", "benchmark-validated")]
    assert validate_claims("Index idea 2 is benchmark-validated. [F13] It scanned 1,824,403 rows. [F3]", ok).dropped == []
    # citing a fact that says something else does not make the claim true
    assert dropped("Index idea 1 is benchmark-validated. [F11]", ok)


@pytest.mark.parametrize(
    "draft",
    [
        'Then run db.orders.createIndex({"status": 1, "customer_id": 1}) in production. [F12]',  # not the fact's index
        "db.orders.drop() would free space. [F3]",
        "Run VACUUM FULL orders first. [F3]",
        "Call pg_terminate_backend(pid) to stop it. [F3]",
        "db.getSiblingDB('admin').dropDatabase() frees space. [F3]",
    ],
)
def test_database_commands_that_are_not_the_facts_own_are_rejected_like_ddl(draft):
    result = validate_claims(draft, LEVEL_FACTS)
    assert not result.ok and "DDL" in result.reason


def test_the_ideas_own_mongodb_command_is_allowed():
    result = validate_claims('The suggested command is db.orders.createIndex({"customer_id": 1}). [F12] It scanned 1,824,403 rows. [F3]', LEVEL_FACTS)
    assert result.ok


def test_a_ratio_is_only_derived_from_a_before_and_an_after_of_the_same_measurement():
    facts = [*FACTS, f(20, "Index build time", 19.5, "ms"), f(21, "p95 before", 800.0, "ms"), f(22, "p95 after", 40.0, "ms")]
    assert dropped("The query is about 38 times faster.", facts)  # 742 ms / the 19.5 ms index build is not a speedup
    assert dropped("It saves 722 ms.", facts)  # 742 ms - 19.5 ms: unrelated measurements
    assert not dropped("The query is about 25 times faster.", facts)  # 742 / 30: median before / median after
    assert not dropped("That saves 712 ms per call.", facts)
    assert not dropped("The 95th percentile is about 20 times faster.", facts)  # 800 / 40
    assert not dropped("The index made it 96% faster.", facts)  # 1 - 30 / 742 is 95.96%
