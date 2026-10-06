import pytest

from querydoctor.explain import Fact
from querydoctor.explain.validator import ValidationResult, validate_claims


def f(i, label, value, unit=None, derivation=None) -> Fact:
    return Fact(id=f"F{i}", label=label, value=value, unit=unit, text=f"{label}: {value}", derivation=derivation)


FACTS = [
    f(1, "Execution time", 742.0, "ms"),
    f(2, "Improvement", 94.5, "%"),
    f(3, "Rows scanned", 1_824_403, "rows"),
    f(4, "Speedup", 7.97, "x"),
    f(5, "Median before", 742.0, "ms"),
    f(6, "Median after", 30.0, "ms"),
    f(7, "Runs", 7),
    f(8, "Table read", "orders"),
    f(9, "Index idea 1 DDL", "CREATE INDEX ON orders (customer_id, created_at DESC)"),
    f(10, "Planner score", 0.95),
]


@pytest.mark.parametrize(
    "sentence",
    [
        "The query took 742 ms to execute.",
        "The index made it 94.5% faster.",
        "About 95% faster in the benchmark.",  # rounding at a coarser displayed precision
        "It scanned 1,824,403 rows.",
        "It scanned about 1.8M rows.",
        "It scanned about 2M rows.",  # 1.82M rounded to the precision displayed
        "The speedup was 7.97x.",
        "That is roughly 8x faster.",
        "The median fell from 742 ms to 30 ms.",
        "Each side was measured 7 times.",
        "The planner scored it 95%.",  # a 0-1 ratio shown as a percentage
        "The query is about 24.7x faster.",  # a quotient of two measurements: 742 / 30
        "That saves 712 ms per call.",  # a difference of two measurements
        "It is 96% faster.",  # 1 - 30/742
    ],
)
def test_supported_claims_pass(sentence):
    result = validate_claims(sentence, FACTS)
    assert result.ok and result.dropped == [] and result.kept_text == sentence


@pytest.mark.parametrize(
    "sentence",
    [
        "The index made it 99% faster.",  # 94.5 is the fact
        "It is 10x faster.",
        "The query took 743 ms.",
        "The index made it 94.4% faster.",  # more precise than the fact allows
        "The query took 742 s.",  # unit confusion: the fact is in milliseconds
        "It scanned 3M rows.",
        "It scanned 1,824,000 rows.",
        "It was measured 8 times.",
    ],
)
def test_unsupported_claims_are_dropped(sentence):
    keep = "The query took 742 ms to execute."
    result = validate_claims(f"{keep} {sentence}", FACTS)
    assert result.dropped == [sentence] and result.kept_text == keep
    assert result.ok  # one bad sentence out of two is not more than half


@pytest.mark.parametrize(
    "sentence",
    [
        "Add an index on customer_id.",
        "The table t1 has the column c2.",
        "The first parameter $1 is a customer.",
        "Rule PG_SEQ_SCAN_FILTER_WASTE fired.",
        "See finding [F3] for details.",
        "This is index idea 2 of the list.",
        "A 3rd option is a partial index.",
        "Version v2 of the plan.",
    ],
)
def test_identifiers_citations_and_enumerations_are_not_claims(sentence):
    assert validate_claims(sentence, FACTS).ok


def test_numbers_inside_identifiers_do_not_count_even_next_to_real_claims():
    result = validate_claims("The scan of orders_2024 took 742 ms.", FACTS)
    assert result.ok and result.dropped == []


def test_unicode_and_fullwidth_digits_are_normalised_before_checking():
    assert validate_claims("The query took ７４２ ms.", FACTS).ok
    bad = validate_claims("Fine sentence about orders. The query took ９９９ ms.", FACTS)
    assert bad.dropped == ["The query took ９９９ ms."] and "Fine sentence" in bad.kept_text


def test_sentences_are_dropped_individually_and_the_rest_survive():
    text = "The query took 742 ms. It touched 5000 rows. The index is 94.5% faster. Nothing else happened."
    result = validate_claims(text, FACTS)
    assert result.dropped == ["It touched 5000 rows."]
    assert result.kept_text == "The query took 742 ms. The index is 94.5% faster. Nothing else happened."
    assert result.ok


def test_a_draft_is_rejected_when_nothing_survives():
    result = validate_claims("It is 12% faster. It uses 99 MB.", FACTS)
    assert not result.ok and result.kept_text == "" and "no sentence" in result.reason.lower()


def test_a_draft_is_rejected_when_more_than_half_of_it_was_dropped():
    result = validate_claims("The query took 742 ms. It is 12% faster. It uses 99 MB.", FACTS)
    assert not result.ok and "more than half" in result.reason.lower()
    assert validate_claims("The query took 742 ms. Also fine. It uses 99 MB. And 12% more.", FACTS).ok  # exactly half is allowed


@pytest.mark.parametrize(
    "text",
    [
        "Run CREATE INDEX ON orders (id) to fix it.",
        "Then DROP TABLE orders.",
        "ALTER TABLE orders ADD COLUMN x int.",
        "TRUNCATE orders and retry.",
        "You could create index idx on orders (customer_id).",
    ],
)
def test_ddl_in_a_draft_rejects_it_outright(text):
    result = validate_claims(f"The query took 742 ms. {text}", FACTS)
    assert not result.ok and "ddl" in result.reason.lower()


def test_quoting_an_index_idea_ddl_from_the_facts_verbatim_is_allowed():
    text = "Index idea 1 is CREATE INDEX ON orders (customer_id, created_at DESC)."
    assert validate_claims(text, FACTS).ok
    assert not validate_claims("Index idea 1 is CREATE INDEX ON orders (everything).", FACTS).ok


def test_ordinary_english_about_creating_things_is_not_ddl():
    assert validate_claims("The index would create extra write work, and you can drop the idea if it hurts.", FACTS).ok


def test_the_result_type_and_determinism():
    a = validate_claims("The query took 742 ms. It is 12% faster.", FACTS)
    assert isinstance(a, ValidationResult) and a == validate_claims("The query took 742 ms. It is 12% faster.", FACTS)


def test_an_empty_text_is_not_ok():
    assert not validate_claims("   ", FACTS).ok
