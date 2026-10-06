from __future__ import annotations

import logging
from collections.abc import Sequence

from querydoctor.diagnosis.models import SEVERITY_RANK, AnalysisContext, DiagnosisRule, Finding
from querydoctor.diagnosis.rules import ALL_RULES, SUPERSEDES
from querydoctor.plan_parser import iter_nodes

log = logging.getLogger(__name__)


def run_diagnosis(context: AnalysisContext, rules: Sequence[DiagnosisRule] | None = None) -> list[Finding]:
    """Evaluate every rule on every applicable node. A crashing rule is logged and skipped, never fatal."""
    active = ALL_RULES if rules is None else rules
    findings: list[Finding] = []
    for node in iter_nodes(context.plan.root):
        for rule in active:
            if rule.supported_node_types and node.node_type not in rule.supported_node_types:
                continue
            try:
                finding = rule.evaluate(node, context)
            except Exception:
                log.exception("diagnosis rule %s crashed on node %s", rule.rule_id, node.node_id)
                continue
            if finding is not None:
                findings.append(finding)
    findings = _drop_superseded(findings)
    findings.sort(key=lambda f: (SEVERITY_RANK[f.severity], f.rule_id, f.node_ids))
    return findings


def _drop_superseded(findings: list[Finding]) -> list[Finding]:
    dropped: set[int] = set()
    for winner in findings:
        losers = SUPERSEDES.get(winner.rule_id)
        if not losers:
            continue
        for i, other in enumerate(findings):
            if other.rule_id in losers and set(other.node_ids) & set(winner.node_ids):
                dropped.add(i)
    return [f for i, f in enumerate(findings) if i not in dropped]
