"""Conservative workload ROI: one winning fix per query, identical indexes consolidated."""
from __future__ import annotations

from querydoctor.benchmark.models import BenchmarkResult
from querydoctor.benchmark.reliability import awards_validation


def rank_fixes(queries, evidence, window_seconds=None):
    """Evidence must already be scoped to one project, engine and source by the caller.

    Savings extrapolate benchmark speedup to observed execution time, not CPU time.
    Missing capture windows never produce a fabricated daily rate.
    """
    winners = {}
    by_fingerprint = {q.fingerprint: q for q in queries}
    for item in evidence:
        query = by_fingerprint.get(item['fingerprint'])
        if query is None:
            continue
        try:
            result = BenchmarkResult.model_validate(item['result'])
        except (ValueError, TypeError):
            continue
        if not awards_validation(result) or result.before.n < 5 or result.after.n < 5:
            continue
        if max(result.before.cv, result.after.cv) > .25:
            continue
        fraction = max(0, min(1, 1 - result.after.median_ms / result.before.median_ms)) if result.before.median_ms else 0
        saved = max(0, query.total_ms) * fraction
        old = winners.get(query.fingerprint)
        if old is None or saved > old['saved_ms']:
            winners[query.fingerprint] = {**item, 'saved_ms': saved}
    grouped = {}
    for fingerprint, item in winners.items():
        key = item['ddl'].strip().rstrip(';')
        group = grouped.setdefault(key, {'ddl': key, 'validation': 'benchmark_validated', 'fingerprints': [], 'analysis_ids': [], 'saved_ms': 0})
        group['fingerprints'].append(fingerprint)
        group['analysis_ids'].append(item['analysis_id'])
        group['saved_ms'] += item['saved_ms']
    fixes = sorted(grouped.values(), key=lambda item: -item['saved_ms'])
    daily_factor = 86400 / window_seconds if window_seconds and window_seconds > 0 else None
    for fix in fixes:
        fix['hours_per_day'] = fix['saved_ms'] / 3600000 * daily_factor if daily_factor else None
    saved = sum(f['saved_ms'] for f in fixes)
    return {'fixes': fixes, 'saved_hours_in_window': saved / 3600000,
            'hours_per_day': saved / 3600000 * daily_factor if daily_factor else None,
            'note': 'Estimated query execution time saved, not CPU time. Extrapolated from isolated sample benchmarks; combined indexes have not been benchmarked together. Sample parameters may not represent production frequency or skew.'}
