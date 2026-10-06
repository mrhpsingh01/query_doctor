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
    r"(?P<unit>MB|GB|KB|milliseconds?|ms|seconds?|secs?|percent|per\s?cent|%|×|x|fold|"
    r"times(?=\s+(?:faster|slower|quicker|as\s|more|less|longer|shorter|bigger|larger|smaller|better|worse))|"
    r"s|rows?|blocks?|minutes?|hours?|days?|weeks?)?(?![\w])"
)
# Forms the pattern above cannot see: a factor written before its number ("x40"), a number without its leading zero
# (".5 seconds"), and numbers spelled out ("ten times faster", "twice as fast", "a tenfold gain"). None can be checked
# against a fact, so a sentence containing one is not supported.
_UNCHECKABLE = re.compile(
    r"(?<![\w$])[x×]\d|(?<![\w$.\d])\.\d"
    r"|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"hundred|thousand|million|half|several|dozens?)\s+(?:times|percent|per\s?cent|fold|seconds?|milliseconds?|minutes?|rows?|x)\b"
    r"|\b(?:twice|thrice|double[sd]?|doubling|triple[sd]?|tripling|halv(?:e|es|ed|ing)|\w+fold)\b",
    re.IGNORECASE,
)
# Wording that promises more than a measurement can: the system prompt forbids it, so the validator enforces it.
_ABSOLUTE = re.compile(
    r"\bguarantee[sd]?\b|\bsafe(?:ly)?\s+(?:in|for|to\s+(?:run|apply|use|create|deploy))\b|\bproduction[- ]safe\b|\brisk[- ]free\b"
    r"|\bno\s+risk\b|\bproven\b|\bwithout\s+(?:any\s+)?risk\b|\bcannot\s+(?:fail|hurt|regress)\b",
    re.IGNORECASE,
)
_LEVEL = re.compile(r"\b(benchmark|planner)[\s_-]*validated\b", re.IGNORECASE)
# A citation after the full stop ("It is fast. [F3] Next one.") belongs to the sentence before it, so never split in front
# of one; a sentence also ends right after its trailing citation.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=(?!\[F\d)[A-Z0-9\[\"'(])|(?<=\])\s+(?=[A-Z0-9\"'(])|\n+")
_DDL = re.compile(
    r"(?-i:\b(?:CREATE|DROP|ALTER|TRUNCATE)\b(?=\s))|\b(?:create|drop|alter|truncate)\s+(?:unique\s+)?(?:index|table|database|schema|view|extension|role|user)\b",
    re.IGNORECASE,
)
# A database command is never part of an explanation unless it is exactly one of the facts (an index idea's own command).
_COMMAND = re.compile(
    r"\bdb\s*\.\s*[\w$.\[\]'\"()-]+\s*\(|\b(?:VACUUM|REINDEX|CLUSTER|GRANT|REVOKE|COPY)\b(?=\s)|\bpg_\w+\s*\(",
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
    unit = re.sub(r"\s", "", unit) if unit else unit
    if unit in ("×", "x", "fold", "times"):
        return "x"
    if unit in ("percent",):
        return "%"
    if unit in ("second", "seconds", "sec", "secs"):
        return "s"
    if unit in ("millisecond", "milliseconds"):
        return "ms"
    if unit in ("minute", "minutes", "hour", "hours", "day", "days", "week", "weeks"):
        return "unsupported"  # no fact is expressed in these units, so such a claim cannot be checked
    if unit in ("row", "rows"):
        return "rows"
    if unit in ("block", "blocks"):
        return "blocks"
    return unit


def _stem(label: str, word: str) -> str | None:
    """The label without ``word`` (before / after), or None if it does not contain it."""
    lowered = label.lower()
    return re.sub(r"\s+", " ", lowered.replace(word, " ")).strip() if word in lowered else None


def _before_after(numeric: list[Fact]) -> list[tuple[Fact, Fact]]:
    """Pairs of facts that are the before and the after of one measurement and share a unit."""
    pairs = []
    for x in numeric:
        stem = _stem(x.label, "before")
        if stem is None:
            continue
        pairs += [(x, y) for y in numeric if y.unit == x.unit and _stem(y.label, "after") == stem]
    return pairs


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
    # derivations: a ratio, a percentage or a difference only between the before and the after of the same measurement
    # ("Median before" / "Median after"); a quotient of two unrelated measurements is not a claim the evidence makes
    for before, after in _before_after(numeric):
        a, b, u = float(before.value), float(after.value), before.unit
        if b <= 0 or not u or u in ("%", "x"):
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
    if unit == "unsupported":
        return False
    claimed = value * scale
    tolerance = 0.5 * (10 ** -decimals) * scale + _EPS
    return any(abs(candidate - claimed) <= tolerance for candidate in _candidates(unit, numeric))


def _unsupported_number(sentence: str, numeric: list[Fact]) -> bool:
    cleaned = _CITATION.sub(" ", unicodedata.normalize("NFKC", sentence))
    cleaned = _ENUMERATION.sub(lambda m: m.group(1), cleaned)
    cleaned = _LIST_MARKER.sub("", cleaned)
    if _UNCHECKABLE.search(cleaned):
        return True
    return any(not _supported(m, numeric) for m in _NUMBER.finditer(cleaned))


def _normal(text: str) -> str:
    return re.sub(r"[\s_-]+", " ", text.lower())


def _unsupported_claim(sentence: str, facts: list[Fact]) -> bool:
    """A promise no measurement makes, or an evidence level the cited facts do not state."""
    if _ABSOLUTE.search(sentence):
        return True
    levels = {_normal(m.group(0)) for m in _LEVEL.finditer(sentence)}
    if not levels:
        return False
    cited = {i for m in _CITATION.finditer(sentence) for i in re.findall(r"F\d+", m.group(0))}
    if not cited:  # an uncited sentence is dropped by the service anyway; the template's own text is cited out of band
        return False
    stated = _normal(" ".join(f"{f.text} {f.value}" for f in facts if f.id in cited))
    return not all(level in stated for level in levels)


def _strip_known_ddl(text: str, facts: list[Fact]) -> str:
    for f in facts:
        if isinstance(f.value, str) and re.match(r"\s*(CREATE\s+INDEX|db\s*\.)", f.value, re.IGNORECASE):
            text = text.replace(f.value, " ")
    return text


def validate_claims(text: str, facts: list[Fact] | EvidencePackage) -> ValidationResult:
    """Keep the sentences whose numbers the facts support; reject the draft when too little is left or it contains DDL."""
    fact_list = _facts(facts)
    numeric = [f for f in fact_list if not isinstance(f.value, str)]
    sentences = _split(text)
    if not sentences:
        return ValidationResult(False, "", [], "The draft was empty.")
    bare = _strip_known_ddl(text, fact_list)
    if _DDL.search(bare) or _COMMAND.search(bare):
        return ValidationResult(
            False, "", [],
            "The draft contained DDL or a database command (CREATE, DROP, ALTER, TRUNCATE, db.<collection>.<method>(), VACUUM and the like), which an explanation must never include.",
        )
    kept, dropped = [], []
    for sentence in sentences:
        bad = _unsupported_number(sentence, numeric) or _unsupported_claim(sentence, fact_list)
        (dropped if bad else kept).append(sentence)
    kept_text = " ".join(kept)
    if not kept:
        return ValidationResult(False, "", dropped, "No sentence survived: every one stated a number the evidence does not support.")
    if len(dropped) * 2 > len(sentences):
        return ValidationResult(False, kept_text, dropped, "More than half of the draft stated numbers the evidence does not support.")
    return ValidationResult(True, kept_text, dropped, None)
