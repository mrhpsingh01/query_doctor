from querydoctor.benchmark.stats import summarize
from querydoctor.ci.runner import classify, render_report


def test_classification_requires_comparable_evidence():
    baseline = summarize([10] * 7)
    assert classify(baseline, summarize([5] * 7), True) == 'OPTIMIZED'
    assert classify(baseline, summarize([15] * 7), True) == 'REGRESSION_DETECTED'
    assert classify(baseline, summarize([10] * 7), True) == 'PASS'
    assert classify(baseline, summarize([1, 20, 2, 30, 3, 25, 1]), True) == 'INSUFFICIENT_EVIDENCE'
    assert classify(baseline, summarize([5] * 7), False) == 'REGRESSION_DETECTED'


def test_high_variance_only_vetoes_overlapping_distributions_not_clearly_separated_ones():
    # Sub-millisecond queries are inherently noisy (CV 30%+) yet a 70x change is unmistakable.
    noisy_fast = summarize([0.10, 0.13, 0.19, 0.09, 0.22, 0.12, 0.11])
    assert noisy_fast.cv > 0.25
    assert classify(noisy_fast, summarize([8.9, 9.0, 9.2, 8.7, 9.4, 9.1, 8.8]), True) == 'REGRESSION_DETECTED'
    assert classify(summarize([8.4, 8.5, 8.6, 8.3, 8.8, 8.5, 8.4]), summarize([0.08, 0.1, 0.17, 0.09, 0.2, 0.1, 0.11]), True) == 'OPTIMIZED'
    # ...but noise that overlaps the baseline stays inconclusive at millisecond scale, and noisy "no change" is not a pass
    noisy_slow = summarize([2.0, 2.6, 3.8, 1.8, 4.4, 2.4, 2.2])
    assert noisy_slow.cv > 0.25
    assert classify(noisy_slow, summarize([2.0, 6.0, 1.8, 5.6, 2.2, 2.4, 5.0]), True) == 'INSUFFICIENT_EVIDENCE'
    assert classify(noisy_slow, summarize([2.2, 2.4, 3.6, 2.0, 4.0, 2.6, 2.4]), True) == 'INSUFFICIENT_EVIDENCE'


def test_an_unseparated_change_below_the_noise_floor_is_a_pass_not_endless_inconclusive():
    # every run of both sides under 1 ms: scheduler jitter dominates, and no separated change exists
    fast = summarize([0.10, 0.13, 0.19, 0.09, 0.22, 0.12, 0.11])
    assert classify(fast, summarize([0.11, 0.12, 0.18, 0.1, 0.2, 0.13, 0.12]), True) == 'PASS'
    assert classify(fast, summarize([0.10, 0.30, 0.09, 0.28, 0.11, 0.12, 0.25]), True) == 'PASS'
    # a separated change is still reported at any scale, and different output is never excused by the floor
    assert classify(fast, summarize([0.9, 0.95, 0.92, 0.91, 0.97, 0.9, 0.93]), True) == 'REGRESSION_DETECTED'
    assert classify(fast, summarize([0.11, 0.12, 0.18, 0.1, 0.2, 0.13, 0.12]), False) == 'REGRESSION_DETECTED'


def test_report_never_calls_missing_evidence_a_pass():
    assert 'Insufficient evidence' in render_report([])
    report = render_report([{'file': '<script>|x', 'outcome': 'INSUFFICIENT_EVIDENCE'}])
    assert '<script>' not in report
    assert 'Not verified' in report
    assert '✅ Identical' not in report

def test_report_distinguishes_migration_latency_from_the_index_experiment():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[4]
    capture = json.loads((root/'apps/web/lib/showcaseCaptured.json').read_text(encoding='utf-8'))['scan']
    benchmark = capture['benchmark']
    report = render_report([{
        'file':'queries/orders.sql', 'outcome':'REGRESSION_DETECTED', 'checksum_match':True,
        'before': summarize([1]*7).model_dump(), 'after': summarize([10]*7).model_dump(), 'bottleneck':'Seq Scan',
        'fixes':[{'ddl':benchmark['ddl'], 'validation':'Benchmark-Validated', 'comparison':capture['comparison'], 'benchmark':benchmark}],
    }])
    assert '1.000 ms (0.0%) → 10.000 ms (0.0%)' in report
    assert 'Isolated index experiment' in report
    assert f"{benchmark['after']['median_ms']:.3f} ms" in report
    assert 'CREATE INDEX CONCURRENTLY ON' in report
    assert 'autocommit_block' in report
