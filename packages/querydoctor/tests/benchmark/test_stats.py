import pytest
from pydantic import ValidationError

from querydoctor.benchmark import (
    BenchmarkConfig,
    improvement_pct,
    is_blocking,
    reliability_warnings,
    summarize,
)


def stats(samples):
    return summarize(samples)


def test_median_p95_and_spread():
    s = summarize([10, 12, 11, 100, 10, 11, 12])
    assert s.n == 7 and s.median_ms == 11 and s.min_ms == 10 and s.max_ms == 100
    assert s.p95_ms == pytest.approx(12 + 0.7 * (100 - 12))  # linear interpolation between the two highest samples
    assert s.mean_ms == pytest.approx(166 / 7)
    assert s.stdev_ms > 0 and s.cv == pytest.approx(s.stdev_ms / s.mean_ms) and s.cv > 0.25


def test_single_sample_has_no_spread():
    s = summarize([42.0])
    assert (s.n, s.median_ms, s.p95_ms, s.stdev_ms, s.cv) == (1, 42.0, 42.0, 0.0, 0.0)


def test_even_count_median_averages_the_middle_pair():
    assert summarize([1, 2, 3, 10]).median_ms == 2.5


def test_empty_samples_are_rejected():
    with pytest.raises(ValueError):
        summarize([])


def test_improvement_percentage():
    assert improvement_pct(742, 41) == pytest.approx(94.47, abs=0.01)
    assert improvement_pct(100, 150) == pytest.approx(-50.0)  # a regression is negative
    assert improvement_pct(0, 10) == 0.0


def test_config_enforces_the_allowed_run_range():
    assert BenchmarkConfig().runs == 7 and BenchmarkConfig().warmups == 1
    for bad in (4, 11, 0):
        with pytest.raises(ValidationError):
            BenchmarkConfig(runs=bad)
    assert BenchmarkConfig(runs=5).runs == 5 and BenchmarkConfig(runs=10).runs == 10


CLEAN = dict(config=BenchmarkConfig(), checksum_match=True, first_run_ratio=1.1)


def test_a_clean_comparison_has_no_warnings():
    assert reliability_warnings(stats([10, 10.2, 9.9, 10.1, 10, 10.1, 9.8]), stats([2, 2.1, 2, 1.9, 2, 2.1, 2]), **CLEAN) == []


def test_high_variance_is_flagged_for_either_side():
    noisy = stats([10, 30, 10, 28, 11, 29, 10])
    steady = stats([10, 10, 10, 10, 10, 10, 10])
    assert any("high variance" in w.lower() for w in reliability_warnings(noisy, steady, **CLEAN))
    assert any("high variance" in w.lower() for w in reliability_warnings(steady, noisy, **CLEAN))


def test_too_few_runs_is_flagged():
    few = stats([10, 10, 10])
    assert any("too few runs" in w.lower() for w in reliability_warnings(few, few, **CLEAN))


def test_different_output_is_flagged_and_blocks_validation():
    ok = stats([10] * 7)
    warnings = reliability_warnings(ok, ok, config=BenchmarkConfig(), checksum_match=False, first_run_ratio=1.0)
    assert any("output differs" in w.lower() for w in warnings)
    assert any(is_blocking(w) for w in warnings)
    assert not any(is_blocking(w) for w in reliability_warnings(stats([10, 30, 10, 28, 11, 29, 10]), ok, **CLEAN))


def test_cache_warming_is_flagged_when_the_first_run_dominates():
    ok = stats([10] * 7)
    warnings = reliability_warnings(ok, ok, config=BenchmarkConfig(), checksum_match=True, first_run_ratio=5.0)
    assert any("cache warming" in w.lower() for w in warnings)


# ---- comparing outputs (review finding I1) ------------------------------------------------------------------------

from querydoctor.benchmark import compare_outputs  # noqa: E402


def test_identical_outputs_match_without_warnings():
    assert compare_outputs("u1", "u1", "o1", "o1") == (True, [])
    assert compare_outputs("u1", "u1", None, None) == (True, [])  # a query without ORDER BY has no ordered checksum


def test_different_rows_are_a_mismatch():
    match, warnings = compare_outputs("u1", "u2", "o1", "o2")
    assert match is False and warnings == []  # the blocking warning comes from reliability_warnings


def test_rows_that_tie_on_the_sort_key_may_legitimately_come_back_in_another_order():
    """ORDER BY on a non-unique key leaves ties in unspecified order: same rows, different order is NOT 'output differs'."""
    match, warnings = compare_outputs("u1", "u1", "o1", "o2")
    assert match is True
    assert len(warnings) == 1 and "order" in warnings[0].lower() and not is_blocking(warnings[0])


# ---- when a benchmark may award validation (review finding I2) -----------------------------------------------------

from querydoctor.benchmark import awards_validation  # noqa: E402
from querydoctor.benchmark.models import BenchmarkResult  # noqa: E402


def result(**over):
    base = dict(
        before=summarize([10.0] * 7), after=summarize([2.0] * 7), before_samples=[], after_samples=[],
        improvement_pct=80.0, index_size_bytes=1, index_build_ms=1.0, plan_fingerprint_before="a", plan_fingerprint_after="b",
        checksum_match=True, warnings=[], pg_version="PostgreSQL 16", settings={}, dataset_snapshot="s", ddl="CREATE INDEX ON t (a)",
        index_used=True,
    )
    return BenchmarkResult(**{**base, **over})


def test_a_clear_comparable_improvement_that_uses_the_index_is_validated():
    assert awards_validation(result()) is True
    assert awards_validation(result(warnings=["Cache warming dominates: the first run was 3.4x the median."])) is True


@pytest.mark.parametrize(
    "over",
    [
        dict(improvement_pct=1.2),  # inside the noise
        dict(improvement_pct=9.9),  # just under the floor
        dict(improvement_pct=-30.0),  # a regression
        dict(index_used=False),  # faster only by chance: the plan never used the index
        dict(checksum_match=False),
        dict(warnings=["Output differs: the query returned different results"]),
        # a 12% "improvement" whose slow runs are no better than a typical original run: the distributions overlap
        dict(improvement_pct=12.0, before=summarize([8, 10, 12, 9, 11, 10, 10]), after=summarize([7, 9, 14, 8, 10, 9, 13])),
    ],
)
def test_noise_regressions_unused_indexes_and_incomparable_results_are_not_validated(over):
    assert awards_validation(result(**over)) is False


def test_high_variance_alone_does_not_block_validation_when_the_runs_are_clearly_separated():
    """A sub-millisecond indexed query has large *relative* jitter (CV ~0.3) yet every indexed run beats every original
    run. Variance only matters when the two distributions overlap, so the warning is shown but does not veto."""
    noisy_but_separated = result(
        before=summarize([9.0, 10.0, 11.0, 10.5, 9.5]), after=summarize([0.2, 0.3, 0.45, 0.25, 0.4]), improvement_pct=96.0,
        warnings=["High variance in the after timings (CV 33%)"],
    )
    assert noisy_but_separated.after.cv > 0.25
    assert awards_validation(noisy_but_separated) is True

def test_too_few_runs_or_different_row_counts_never_award_validation():
    from querydoctor.benchmark.models import RunSample
    assert not awards_validation(result(before=summarize([10] * 4)))
    assert not awards_validation(result(before_samples=[RunSample(execution_ms=10,rows=2)], after_samples=[RunSample(execution_ms=2,rows=3)]))
