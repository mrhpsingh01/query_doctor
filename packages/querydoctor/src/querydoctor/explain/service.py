"""Explanation orchestration: build_evidence -> draft -> validate, with the template as the always-available fallback."""

from __future__ import annotations

import re

from querydoctor.explain.facts import EvidencePackage, build_evidence
from querydoctor.explain.ollama import ExplainerUnavailable, OllamaExplainer
from querydoctor.explain.template import Explanation, Sentence, explain_template
from querydoctor.explain.validator import _CITATION, _split, validate_claims

_SQL = re.compile(r"\bSELECT\b.+\bFROM\b|\bINSERT\s+INTO\b|\bUPDATE\b.+\bSET\b|\bDELETE\s+FROM\b", re.IGNORECASE | re.DOTALL)
_FACT_ID = re.compile(r"F\d+")


def _cited(sentence: str, pkg: EvidencePackage) -> list[str]:
    known = pkg.by_id()
    ids = [i for m in _CITATION.finditer(sentence) for i in _FACT_ID.findall(m.group(0))]
    return [i for i in dict.fromkeys(ids) if i in known]


def _fallback(pkg: EvidencePackage, reason: str, dropped: list[str] | None = None) -> Explanation:
    return explain_template(pkg).model_copy(update={"fallback_reason": reason, "dropped": dropped or []})


def _from_draft(pkg: EvidencePackage, draft: str, model: str) -> Explanation:
    if _SQL.search(draft):
        return _fallback(pkg, "The language model's draft contained SQL, so the template explanation is shown instead.")
    result = validate_claims(draft, pkg)
    if not result.ok and not result.kept_text:
        return _fallback(pkg, f"{result.reason} The template explanation is shown instead.", result.dropped)
    if not result.ok and result.reason and "DDL" in result.reason:
        return _fallback(pkg, f"{result.reason} The template explanation is shown instead.", result.dropped)
    kept: list[Sentence] = []
    dropped = list(result.dropped)
    for sentence in _split(result.kept_text):
        ids = _cited(sentence, pkg)
        if ids:
            kept.append(Sentence(text=_CITATION.sub("", sentence).strip(), fact_ids=ids))
        else:  # an uncited sentence cannot be traced to evidence
            dropped.append(sentence)
    total = len(kept) + len(dropped)
    if not kept:
        return _fallback(pkg, "No sentence survived: none cited the evidence. The template explanation is shown instead.", dropped)
    if len(dropped) * 2 > total:
        return _fallback(pkg, "More than half of the draft was not supported by the evidence. The template explanation is shown instead.", dropped)
    text = " ".join(f"{s.text} [{', '.join(s.fact_ids)}]" for s in kept)
    return Explanation(text=text, sentences=kept, source=f"ollama:{model}", dropped=dropped, fallback_reason=None)


def explain(analysis: dict, kind: str, *, ollama: OllamaExplainer | None = None) -> Explanation:
    """The explanation of ``kind`` for an analysis. Never raises for model problems: it falls back and says why."""
    pkg = build_evidence(analysis, kind)
    if ollama is None or not ollama.enabled or not pkg.facts:
        return explain_template(pkg)
    try:
        draft = ollama.draft(pkg)
    except ExplainerUnavailable as exc:
        return _fallback(pkg, f"{exc} The template explanation is shown instead.")
    except Exception:  # a bug in the draft path must never become an error response
        return _fallback(pkg, "The language model could not be used. The template explanation is shown instead.")
    return _from_draft(pkg, draft, ollama.model)
