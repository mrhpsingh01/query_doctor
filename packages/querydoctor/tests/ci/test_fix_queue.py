from types import SimpleNamespace
from querydoctor.workload.fix_queue import rank_fixes
from querydoctor.benchmark.stats import summarize


def evidence(fp, ddl='CREATE INDEX ON orders (customer_id)', after=2, checksum=True):
    before_stats, after_stats = summarize([10] * 7), summarize([after] * 7)
    return dict(fingerprint=fp, ddl=ddl, analysis_id=1, result=dict(
        before=before_stats.model_dump(), after=after_stats.model_dump(), before_samples=[], after_samples=[],
        improvement_pct=(10-after)*10, index_size_bytes=100, index_build_ms=1,
        plan_fingerprint_before='before', plan_fingerprint_after='after', checksum_match=checksum, index_used=True,
        warnings=[], pg_version='16', settings={}, dataset_snapshot='test', ddl=ddl))


def test_consolidates_shared_indexes_without_double_counting_queries():
    queries = [SimpleNamespace(fingerprint=fp, total_ms=3600000) for fp in ['a', 'b']]
    queue = rank_fixes(queries, [evidence('a'), evidence('b'), evidence('a', 'another', after=5)], 86400)
    assert len(queue['fixes']) == 1
    assert queue['hours_per_day'] == 1.6
    assert queue['fixes'][0]['fingerprints'] == ['a', 'b']


def test_unverified_and_mismatched_results_cannot_contribute():
    query = SimpleNamespace(fingerprint='a', total_ms=3600000)
    assert rank_fixes([query], [evidence('a', checksum=False)])['fixes'] == []
    queue = rank_fixes([query], [evidence('a')])
    assert queue['hours_per_day'] is None
    assert queue['saved_hours_in_window'] == .8
