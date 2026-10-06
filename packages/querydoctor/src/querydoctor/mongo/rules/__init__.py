from __future__ import annotations

import logging

from querydoctor.diagnosis.models import SEVERITY_RANK, Finding
from querydoctor.mongo.rules.aggregation import LargeLookup, StageOrder, UnboundedAggregation
from querydoctor.mongo.rules.base import CollectionInfo, IndexSpec, MongoContext, MongoThresholds
from querydoctor.mongo.rules.indexes import RedundantIndex, UnselectiveIndex
from querydoctor.mongo.rules.scans import CollScan, KeyAmplification, ScanAmplification
from querydoctor.mongo.rules.sorting import BlockingSort, IndexOrder

log = logging.getLogger(__name__)

MONGO_RULES = [
    CollScan(), ScanAmplification(), KeyAmplification(), BlockingSort(), IndexOrder(), UnboundedAggregation(), LargeLookup(),
    UnselectiveIndex(), RedundantIndex(), StageOrder(),
]


def run_mongo_diagnosis(context: MongoContext, rules=None) -> list[Finding]:
    """Evaluate every MongoDB rule. A rule that crashes is logged and skipped, never fatal."""
    findings: list[Finding] = []
    for rule in MONGO_RULES if rules is None else rules:
        try:
            findings.extend(rule.evaluate(context))
        except Exception:
            log.exception("mongo diagnosis rule %s crashed", rule.rule_id)
    findings.sort(key=lambda f: (SEVERITY_RANK[f.severity], f.rule_id, f.node_ids))
    return findings


__all__ = ["MONGO_RULES", "CollectionInfo", "IndexSpec", "MongoContext", "MongoThresholds", "run_mongo_diagnosis"]
