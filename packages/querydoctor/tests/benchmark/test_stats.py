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
