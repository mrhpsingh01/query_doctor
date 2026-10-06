"""Pure workload analysis: which query to optimize first, what regressed, which plans changed."""

from __future__ import annotations

from pydantic import BaseModel

from querydoctor.workload.models import WorkloadQuery

MIN_UNSTABLE_CALLS = 20
MIN_REGRESSION_CALLS = 20
MIN_REGRESSION_RATIO = 1.5
PANEL_SIZE = 10


class RankedQuery(WorkloadQuery):
    rank: int
    share: float  # this query's share of the snapshot's total execution time


class Regression(BaseModel):
    fingerprint: str
    baseline_mean_ms: float  # the earlier snapshot's cumulative mean
    recent_mean_ms: float  # mean over the calls made since that snapshot
    ratio: float
    recent_calls: int


class PlanChange(BaseModel):
    before_analysis_id: int
    after_analysis_id: int
    before_fingerprint: str
    after_fingerprint: str
    before_operators: list[str]
    after_operators: list[str]


class Panels(BaseModel):
    highest_total_time: list[RankedQuery]
    slowest_p95: list[RankedQuery]
    most_frequent: list[RankedQuery]
    highest_io: list[RankedQuery]
    most_unstable: list[RankedQuery]
    largest_regression: list[Regression]
    missing_validation: list[RankedQuery]


def rank(queries: list[WorkloadQuery]) -> list[RankedQuery]:
    """Order by total database time (mean x calls): the guide's 'optimize this first' order."""
    ordered = sorted(queries, key=lambda x: x.total_ms, reverse=True)
    grand_total = sum(x.total_ms for x in ordered)
    return [
        RankedQuery(**x.model_dump(), rank=i, share=(x.total_ms / grand_total if grand_total > 0 else 0.0))
        for i, x in enumerate(ordered, start=1)
    ]


def detect_regressions(
    before: list[WorkloadQuery],
    after: list[WorkloadQuery],
    *,
    min_calls: int = MIN_REGRESSION_CALLS,
    min_ratio: float = MIN_REGRESSION_RATIO,
) -> list[Regression]:
    """Compare two snapshots. pg_stat_statements counters are cumulative, so the *recent* mean is the interval between
    them and the baseline is the earlier snapshot's cumulative mean."""
    earlier = {x.fingerprint: x for x in before}
    found: list[Regression] = []
    for later in after:
        prior = earlier.get(later.fingerprint)
        if prior is None or prior.mean_ms <= 0:
            continue
        new_calls = later.calls - prior.calls
        interval_total = later.total_ms - prior.total_ms
        if new_calls < min_calls or interval_total < 0:  # too few calls, or the counters were reset in between
            continue
        recent = interval_total / new_calls
        ratio = recent / prior.mean_ms
        if ratio >= min_ratio:
            found.append(
                Regression(
                    fingerprint=later.fingerprint, baseline_mean_ms=prior.mean_ms, recent_mean_ms=recent, ratio=ratio,
                    recent_calls=new_calls,
                )
            )
    return sorted(found, key=lambda r: r.ratio, reverse=True)


def build_panels(
    queries: list[WorkloadQuery],
    *,
    validated_fingerprints: set[str],
    regressions: list[Regression],
    size: int = PANEL_SIZE,
) -> Panels:
    ranked = rank(queries)

    def top(key, predicate=lambda _x: True) -> list[RankedQuery]:
        return sorted((x for x in ranked if predicate(x)), key=key, reverse=True)[:size]

    return Panels(
        highest_total_time=top(lambda x: x.total_ms),
        slowest_p95=top(lambda x: x.p95_est_ms),
        most_frequent=top(lambda x: x.calls),
        highest_io=top(lambda x: x.shared_read_blocks + x.temp_blocks, lambda x: x.shared_read_blocks + x.temp_blocks > 0),
        most_unstable=top(
            lambda x: x.stddev_ms / x.mean_ms, lambda x: x.calls >= MIN_UNSTABLE_CALLS and x.mean_ms > 0
        ),
        largest_regression=sorted(regressions, key=lambda r: r.ratio, reverse=True)[:size],
        missing_validation=top(lambda x: x.total_ms, lambda x: x.fingerprint not in validated_fingerprints),
    )


def plan_changes(analyses: list[dict]) -> list[PlanChange]:
    """Switches of plan fingerprint between consecutive analyses of one query (oldest first)."""
    seen = [a for a in analyses if a.get("plan_fingerprint")]
    return [
        PlanChange(
            before_analysis_id=prev["id"], after_analysis_id=cur["id"], before_fingerprint=prev["plan_fingerprint"],
            after_fingerprint=cur["plan_fingerprint"], before_operators=list(prev.get("operators", [])),
            after_operators=list(cur.get("operators", [])),
        )
        for prev, cur in zip(seen, seen[1:])
        if prev["plan_fingerprint"] != cur["plan_fingerprint"]
    ]
