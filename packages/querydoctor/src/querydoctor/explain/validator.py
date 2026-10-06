"""The claim validator: no number in an explanation may be invented.

Every standalone number a draft states (with its unit) must match a fact, or a documented derivation of facts (a ratio
or difference of two measurements in the same unit, a 0-1 score shown as a percentage), at the precision the draft
displays: ``7.97`` may be written ``8x`` or ``7.97x`` but not ``7.9x``. Digits inside identifiers (``customer_id``,
``t1``, ``$1``), citations (``[F3]``) and enumerations (``idea 2``) are not claims. A sentence with an unsupported number
is dropped; a draft is rejected when nothing survives, when more than half of it was dropped, or when it contains DDL.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from querydoctor.explain.facts import EvidencePackage, Fact

_EPS = 1e-9
_CITATION = re.compile(r"\[F\d+(?:\s*,\s*F\d+)*\]")
_ENUMERATION = re.compile(r"\b(idea|ideas|option|options|step|steps|rule|finding|findings)\s+\d+", re.IGNORECASE)
_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s+")
_NUMBER = re.compile(
    r"(?<![\w$.])(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?(?:(?P<scale>[kM])(?![A-Za-z]))?\s?"
    r"(?P<unit>MB|GB|KB|ms|%|×|x|s|rows?|blocks?)?(?![\w])"
)
# A citation after the full stop ("It is fast. [F3] Next one.") belongs to the sentence before it, so never split in front
# of one; a sentence also ends right after its trailing citation.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=(?!\[F\d)[A-Z0-9\[\"'(])|(?<=\])\s+(?=[A-Z0-9\"'(])|\n+")
_DDL = re.compile(
    r"(?-i:\b(?:CREATE|DROP|ALTER|TRUNCATE)\b(?=\s))|\b(?:create|drop|alter|truncate)\s+(?:unique\s+)?(?:index|table|database|schema|view|extension|role|user)\b",
    re.IGNORECASE,
)
_SCALES = {"k": 1e3, "M": 1e6}
_BYTES = {"KB": 1 / 1024, "MB": 1.0, "GB": 1024.0}  # to megabytes


@dataclass
class ValidationResult:
    ok: bool
    kept_text: str
    dropped: list[str] = field(default_factory=list)
    reason: str | None = None


def _facts(facts: list[Fact] | EvidencePackage) -> list[Fact]:
    return facts.facts if isinstance(facts, EvidencePackage) else list(facts)


def _split(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text.strip()) if s and s.strip()]


def _canonical_unit(unit: str | None) -> str | None:
    if unit in ("×", "x"):
        return "x"
    if unit in ("row", "rows"):
        return "rows"
    if unit in ("block", "blocks"):
        return "blocks"
    return unit


def _candidates(unit: str | None, numeric: list[Fact]) -> list[float]:
    """Every value a claim in ``unit`` may legitimately equal (in the claim's unit)."""
    out: list[float] = []
    values = [(f.value, f.unit) for f in numeric]
    if unit is None:  # a bare number may only be a count or a row count, never a time, percentage or factor
        return [float(v) for v, u in values if u in (None, "rows")]
    if unit == "%":
        out += [float(v) for v, u in values if u == "%"]
        out += [float(v) * 100 for v, u in values if u is None and 0 <= float(v) <= 1]
    if unit in ("ms", "s"):
        scale = 1.0 if unit == "ms" else 1 / 1000
        out += [float(v) * scale for v, u in values if u == "ms"]
        if unit == "s":
            out += [float(v) for v, u in values if u == "s"]
    if unit in _BYTES:
        out += [float(v) / _BYTES[unit] for v, u in values if u in _BYTES for v in [float(v) * _BYTES[u]]]
    if unit in ("x", "rows", "blocks"):
        out += [float(v) for v, u in values if u == unit]
    # derivations between two measurements of the same unit
    by_unit: dict[str, list[float]] = {}
    for v, u in values:
        if u and u not in ("%", "x"):
            by_unit.setdefault(u, []).append(float(v))
    for u, vals in by_unit.items():
        for i, a in enumerate(vals):
            for j, b in enumerate(vals):
                if i == j or b <= 0:
                    continue
                if unit == "x":
                    out.append(a / b)
                if unit == "%":
                    out += [100 * a / b, 100 * (1 - b / a) if a > 0 else 0.0]
                if unit in ("ms", "s") and u == "ms" and a > b:
                    out.append((a - b) * (1.0 if unit == "ms" else 1 / 1000))
                if unit in ("rows", "blocks") and u == unit and a > b:
                    out.append(a - b)
    return out


def _supported(claim: re.Match, numeric: list[Fact]) -> bool:
    raw = claim.group("num")
    value = float(raw.replace(",", ""))
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    scale = _SCALES.get(claim.group("scale") or "", 1.0)
    unit = _canonical_unit(claim.group("unit"))
    claimed = value * scale
    tolerance = 0.5 * (10 ** -decimals) * scale + _EPS
    return any(abs(candidate - claimed) <= tolerance for candidate in _candidates(unit, numeric))


def _unsupported_number(sentence: str, numeric: list[Fact]) -> bool:
    cleaned = _CITATION.sub(" ", unicodedata.normalize("NFKC", sentence))
    cleaned = _ENUMERATION.sub(lambda m: m.group(1), cleaned)
    cleaned = _LIST_MARKER.sub("", cleaned)
    return any(not _supported(m, numeric) for m in _NUMBER.finditer(cleaned))


def _strip_known_ddl(text: str, facts: list[Fact]) -> str:
    for f in facts:
        if isinstance(f.value, str) and re.match(r"\s*CREATE\s+INDEX", f.value, re.IGNORECASE):
            text = text.replace(f.value, " ")
    return text


def validate_claims(text: str, facts: list[Fact] | EvidencePackage) -> ValidationResult:
    """Keep the sentences whose numbers the facts support; reject the draft when too little is left or it contains DDL."""
    fact_list = _facts(facts)
    numeric = [f for f in fact_list if not isinstance(f.value, str)]
    sentences = _split(text)
    if not sentences:
        return ValidationResult(False, "", [], "The draft was empty.")
    if _DDL.search(_strip_known_ddl(text, fact_list)):
        return ValidationResult(False, "", [], "The draft contained DDL (CREATE, DROP, ALTER or TRUNCATE), which an explanation must never include.")
    kept, dropped = [], []
    for sentence in sentences:
        (dropped if _unsupported_number(sentence, numeric) else kept).append(sentence)
    kept_text = " ".join(kept)
    if not kept:
        return ValidationResult(False, "", dropped, "No sentence survived: every one stated a number the evidence does not support.")
    if len(dropped) * 2 > len(sentences):
        return ValidationResult(False, kept_text, dropped, "More than half of the draft stated numbers the evidence does not support.")
    return ValidationResult(True, kept_text, dropped, None)
