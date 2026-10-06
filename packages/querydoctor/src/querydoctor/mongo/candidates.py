"""Index candidates for MongoDB: the Equality-Sort-Range rule, pruned against the indexes that already exist.

A candidate is only ever a *suggestion*: MongoDB has no hypothetical indexes, so the evidence levels are ``suggested`` and
(after a real benchmark in a sandbox) ``benchmark_validated``. The ``createIndex`` call is built from the candidate's
structured ``columns``; the ``ddl`` string is for display and is never parsed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from querydoctor.candidates.models import IndexCandidate, IndexColumn
from querydoctor.diagnosis.models import Finding
from querydoctor.mongo.query import ValidatedMongoQuery
from querydoctor.mongo.rules.base import CollectionInfo
from querydoctor.mongo.rules.sorting import RANGE_OPS

MAX_KEY_FIELDS = 4
RELEVANT_RULES = {
    "MONGO_COLLSCAN", "MONGO_SCAN_AMPLIFICATION", "MONGO_KEY_AMPLIFICATION", "MONGO_BLOCKING_SORT", "MONGO_INDEX_ORDER", "MONGO_LARGE_LOOKUP",
}
_FIELD = re.compile(r"^[^$\x00][^\x00]{0,199}$")


def _flatten(filter_: dict[str, Any]) -> dict[str, Any]:
    """Top-level conditions with ``$and`` flattened (``$or`` and other operators do not shape a single index)."""
    out: dict[str, Any] = {}
    for key, value in filter_.items():
        if key == "$and" and isinstance(value, list):
            for part in value:
                if isinstance(part, dict):
                    out.update(_flatten(part))
        elif not key.startswith("$"):
            out[key] = value
    return out


def _classify(filter_: dict[str, Any]) -> tuple[list[str], list[str]]:
    equality: list[str] = []
    ranges: list[str] = []
    for field, condition in _flatten(filter_).items():
        if isinstance(condition, dict):
            ops = set(condition)
            if ops & RANGE_OPS:
                ranges.append(field)
            elif ops and ops <= {"$eq", "$in"}:
                equality.append(field)
            # $ne, $regex, $exists, $not...: an index cannot narrow these well, so they do not shape the key
        else:
            equality.append(field)
    return equality, ranges


def _effective(vq: ValidatedMongoQuery) -> tuple[dict[str, Any], dict[str, int]]:
    """The filter and sort an index can serve: a find's own, or the leading ``$match`` (and ``$sort`` right after it)."""
    q = vq.query
    if q.kind == "find":
        return q.filter, q.sort or {}
    pipeline = q.pipeline or []
    filter_: dict[str, Any] = {}
    sort: dict[str, int] = {}
    rest = pipeline
    if pipeline and "$match" in pipeline[0] and isinstance(pipeline[0]["$match"], dict):
        filter_, rest = pipeline[0]["$match"], pipeline[1:]
    if rest and "$sort" in rest[0] and isinstance(rest[0]["$sort"], dict):
        sort = {k: v for k, v in rest[0]["$sort"].items() if v in (1, -1)}
    if not filter_:  # $group by a field, then $match on the group key: the match can move first and use an index
        for earlier, later in zip(pipeline, pipeline[1:]):
            group = earlier.get("$group") if isinstance(earlier, dict) else None
            match = later.get("$match") if isinstance(later, dict) else None
            key = group.get("_id") if isinstance(group, dict) else None
            if isinstance(key, str) and key.startswith("$") and isinstance(match, dict) and set(match) == {"_id"}:
                return {key[1:]: match["_id"]}, sort
    return filter_, sort


def esr_keys(filter_: dict[str, Any], sort: dict[str, int]) -> list[tuple[str, int]]:
    """Equality fields, then sort fields, then range fields; each field once; at most ``MAX_KEY_FIELDS``."""
    equality, ranges = _classify(filter_)
    keys: list[tuple[str, int]] = []
    seen: set[str] = set()
    for field in equality:
        if field not in seen:
            keys.append((field, 1))
            seen.add(field)
    for field, direction in sort.items():
        if field not in seen:
            keys.append((field, int(direction)))
            seen.add(field)
    for field in ranges:
        if field not in seen:
            keys.append((field, 1))
            seen.add(field)
    return [k for k in keys if _FIELD.match(k[0])][:MAX_KEY_FIELDS]


def display_ddl(collection: str, keys: Sequence[tuple[str, int]]) -> str:
    return f"db.{collection}.createIndex({json.dumps(dict(keys), separators=(', ', ': '))})"


def _candidate(collection: str, keys: list[tuple[str, int]], rationale: list[str], rule_ids: list[str]) -> IndexCandidate:
    ddl = display_ddl(collection, keys)
    return IndexCandidate(
        id=IndexCandidate.make_id(ddl), table=collection, columns=[IndexColumn(name=f, desc=d == -1) for f, d in keys], ddl=ddl,
        rationale=rationale, source_rule_ids=rule_ids, validation="suggested",
    )


def generate_mongo_candidates(vq: ValidatedMongoQuery, collections: dict[str, CollectionInfo], findings: Sequence[Finding]) -> list[IndexCandidate]:
    rule_ids = sorted({f.rule_id for f in findings if f.rule_id in RELEVANT_RULES})
    out: dict[str, IndexCandidate] = {}

    filter_, sort = _effective(vq)
    keys = esr_keys(filter_, sort)
    if keys:
        equality, ranges = _classify(filter_)
        reasons = [f"equality on {f}" for f, _d in keys if f in equality]
        reasons += [f"ORDER BY {f} {'DESC' if d == -1 else 'ASC'} can follow the index order" for f, d in keys if f in sort]
        reasons += [f"range on {f}" for f, _d in keys if f in ranges and f not in sort]
        if any(f.rule_id == "MONGO_INDEX_ORDER" for f in findings):
            reasons.append("the existing compound index has the range field before the equality field; equality, sort, range is the efficient order")
        candidate = _candidate(vq.query.collection, keys, reasons, rule_ids)
        out[candidate.id] = candidate

    for f in findings:  # a $lookup into an unindexed field is fixed on the *target* collection
        if f.rule_id == "MONGO_LARGE_LOOKUP":
            target, field = f.evidence.get("from"), f.evidence.get("foreign_field")
            if target and field and _FIELD.match(field):
                candidate = _candidate(target, [(field, 1)], [f"{target}.{field} is the join field of a $lookup"], ["MONGO_LARGE_LOOKUP"])
                out.setdefault(candidate.id, candidate)
    return list(out.values())


def _keys(candidate: IndexCandidate) -> list[tuple[str, int]]:
    return [(c.name, -1 if c.desc else 1) for c in candidate.columns]


def prune_mongo_redundant(
    candidates: list[IndexCandidate], collections: dict[str, CollectionInfo]
) -> tuple[list[IndexCandidate], list[tuple[IndexCandidate, str]]]:
    """Drop candidates an existing index (or a longer candidate) already provides. Returns ``(kept, [(dropped, reason)])``."""
    kept: list[IndexCandidate] = []
    pruned: list[tuple[IndexCandidate, str]] = []
    for c in candidates:
        keys = _keys(c)
        info = collections.get(c.table)
        existing = next((i for i in (info.indexes if info else []) if [tuple(k) for k in i.keys[: len(keys)]] == keys), None)
        if keys == [("_id", 1)]:
            pruned.append((c, "every collection already has an index on _id"))
        elif existing is not None:
            pruned.append((c, f"the existing index {existing.name} already starts with these fields"))
        else:
            longer = next((o for o in candidates if o is not c and o.table == c.table and _keys(o)[: len(keys)] == keys and len(o.columns) > len(keys)), None)
            if longer is not None:
                pruned.append((c, "a longer candidate index starts with these fields and serves the same queries"))
            else:
                kept.append(c)
    return kept, pruned


def build_create_index(candidate: IndexCandidate) -> list[tuple[str, int]]:
    """The ``createIndex`` key specification, from the candidate's structure (the display string is never parsed)."""
    keys = _keys(candidate)
    if not keys or len(keys) > MAX_KEY_FIELDS:
        raise ValueError(f"an index needs between 1 and {MAX_KEY_FIELDS} key fields")
    for name, _direction in keys:
        if not _FIELD.match(name):
            raise ValueError(f"{name!r} is not a usable index field name")
    return keys
