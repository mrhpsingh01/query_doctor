"""Validate a MongoDB query spec before anything touches a server.

Only a read-only ``find`` or ``aggregate`` passes. JavaScript execution (``$where``, ``$function``, ``$accumulator``),
writes (``$out``, ``$merge``, at any depth), administrative stages and unknown keys are refused. Values arrive as
``{"$param": n}`` placeholders replaced by validated scalars, so a value can never change the structure of a query.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from querydoctor.mongo.query import MongoQuery, ValidatedMongoQuery
from querydoctor.safety import ValidationError

MAX_BYTES = 50_000
MAX_DEPTH = 20
MAX_LIMIT = 10_000
MAX_SKIP = 1_000_000
MAX_REGEX = 200
MAX_PARAM_CHARS = 1_000
MAX_COLLECTION_CHARS = 120

FIND_KEYS = {"find", "filter", "sort", "projection", "limit", "skip"}
AGGREGATE_KEYS = {"aggregate", "pipeline"}
DENIED_OPERATORS = {"$where", "$function", "$accumulator"}  # server-side JavaScript
DENIED_WRITES = {"$out", "$merge"}
STAGE_ALLOWLIST = {
    "$match", "$group", "$sort", "$limit", "$skip", "$project", "$unwind", "$lookup", "$count", "$addFields", "$set", "$unset",
    "$facet", "$bucket", "$bucketAuto", "$sample", "$sortByCount", "$replaceRoot", "$replaceWith",
}
STRUCTURAL_KEYS = {"find", "aggregate", "from", "as", "localField", "foreignField"}
_COLLECTION = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")
_REGEX_OPTIONS = set("imsx")


class MongoValidationError(ValidationError):
    pass


def _fail(code: str, message: str) -> MongoValidationError:
    return MongoValidationError(code, message)


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _fail("duplicate_keys", f"The key {key!r} appears twice in the same object, which drivers treat differently.")
        out[key] = value
    return out


def _no_constants(name: str) -> Any:
    raise _fail("invalid_json", f"{name} is not valid JSON.")


def _depth(value: Any, limit: int) -> int:
    """Nesting depth of dicts and lists, computed without recursion so hostile input cannot exhaust the stack."""
    deepest = 0
    stack = [(value, 1)]
    while stack:
        item, level = stack.pop()
        if isinstance(item, dict):
            deepest = max(deepest, level)
            stack += [(v, level + 1) for v in item.values() if isinstance(v, (dict, list))]
        elif isinstance(item, list):
            deepest = max(deepest, level)
            stack += [(v, level + 1) for v in item if isinstance(v, (dict, list))]
        if deepest > limit:
            return deepest
    return deepest


def _scan(value: Any) -> None:
    """Refuse JavaScript and write operators anywhere in the structure; check regular expressions."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                if key in DENIED_OPERATORS:
                    raise _fail("denied_operator", f"{key} runs JavaScript on the server and is not allowed.")
                if key in DENIED_WRITES:
                    raise _fail("denied_stage", f"{key} writes data and is not allowed.")
                if key == "$regex":
                    _check_regex(child, item.get("$options"))
                if "\x00" in key:
                    raise _fail("bad_filter", "Field names may not contain NUL characters.")
                stack.append(child)
        elif isinstance(item, list):
            stack.extend(item)


def _check_regex(pattern: Any, options: Any) -> None:
    if not isinstance(pattern, str) or len(pattern) > MAX_REGEX:
        raise _fail("bad_regex", f"A $regex must be a string of at most {MAX_REGEX} characters.")
    if options is not None and (not isinstance(options, str) or not set(options) <= _REGEX_OPTIONS):
        raise _fail("bad_regex", "$options may only contain the flags i, m, s and x.")


def _check_collection(name: Any) -> str:
    if not isinstance(name, str) or not name or len(name) > MAX_COLLECTION_CHARS or not _COLLECTION.match(name) or name.startswith("system."):
        raise _fail("bad_collection", "The collection name is missing, too long, or not an ordinary collection name.")
    return name


def _check_pipeline(pipeline: Any, collections: list[str]) -> None:
    if not isinstance(pipeline, list) or not pipeline:
        raise _fail("bad_pipeline", "An aggregate needs a non-empty pipeline (a list of stages).")
    for stage in pipeline:
        if not isinstance(stage, dict) or len(stage) != 1:
            raise _fail("bad_pipeline", "Each pipeline stage must be an object with exactly one stage operator.")
        ((name, body),) = stage.items()
        if name not in STAGE_ALLOWLIST:
            if name.startswith("$"):
                raise _fail("denied_stage", f"The stage {name} is not allowed. Allowed stages: {', '.join(sorted(STAGE_ALLOWLIST))}.")
            raise _fail("bad_pipeline", "Each pipeline stage must be a stage operator starting with $.")
        if name == "$lookup":
            if not isinstance(body, dict):
                raise _fail("bad_pipeline", "$lookup takes an object.")
            target = _check_collection(body.get("from"))
            if target not in collections:
                collections.append(target)
            if "pipeline" in body:
                _check_pipeline(body["pipeline"], collections)
        elif name == "$facet":
            if not isinstance(body, dict) or not body:
                raise _fail("bad_pipeline", "$facet takes an object of named pipelines.")
            for sub in body.values():
                _check_pipeline(sub, collections)
        elif name == "$sort" and (not isinstance(body, dict) or not body):
            raise _fail("bad_sort", "$sort takes an object of field directions.")


def _placeholders(value: Any, found: list[int]) -> None:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if "$param" in item:
                n = item["$param"]
                if len(item) != 1 or isinstance(n, bool) or not isinstance(n, int) or n < 1:
                    raise _fail("bad_param", 'A placeholder is exactly {"$param": n} with n a whole number from 1.')
                found.append(n)
            else:
                stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def _check_scalar(value: Any, index: int) -> Any:
    ok = value is None or isinstance(value, (bool, int)) or (isinstance(value, float) and math.isfinite(value))
    if isinstance(value, str):
        ok = len(value) <= MAX_PARAM_CHARS
    if not ok:
        raise _fail("bad_param", f"Parameter {index} must be a string, number, boolean or null (at most {MAX_PARAM_CHARS} characters).")
    return value


def _substitute(value: Any, params: list[Any]) -> Any:
    if isinstance(value, dict):
        if "$param" in value:
            return params[value["$param"] - 1]
        return {k: _substitute(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, params) for v in value]
    return value


def _is_literal(value: Any) -> bool:
    return not isinstance(value, (dict, list)) or (isinstance(value, dict) and set(value) == {"$param"})


def _shape(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$param"}:
            return "?"
        return {k: _shape(v, k) for k, v in value.items()}
    if isinstance(value, list):
        if value and all(_is_literal(v) for v in value):
            return ["?"]  # a list of literals is one shape however long it is
        return [_shape(v, key) for v in value]
    if isinstance(value, str) and (value.startswith("$") or key in STRUCTURAL_KEYS):
        return value  # field paths, variables and collection/field names are structure
    return "?"


def normalize_shape(query: MongoQuery) -> str:
    """The query with every literal replaced by ``?`` (collection and field names stay)."""
    spec: dict[str, Any] = {query.kind: query.collection}
    if query.kind == "find":
        spec.update(filter=query.filter, sort=query.sort, projection=query.projection, limit=query.limit, skip=query.skip)
    else:
        spec["pipeline"] = query.pipeline
    spec = {k: v for k, v in spec.items() if v is not None}
    return json.dumps(_shape(spec), separators=(",", ":"), ensure_ascii=False)


def _parse(text: str, max_bytes: int, max_depth: int) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise _fail("invalid_json", 'The query is empty. Write a JSON object such as {"find": "orders", "filter": {}}.')
    if len(text.encode("utf-8")) > max_bytes:
        raise _fail("too_large", f"The query is larger than {max_bytes:,} bytes.")
    try:
        spec = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constants)
    except json.JSONDecodeError as exc:
        raise _fail("invalid_json", f"The query is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno}).") from exc
    except RecursionError as exc:
        raise _fail("too_deep", f"The query is nested more than {max_depth} levels deep.") from exc
    if not isinstance(spec, dict):
        raise _fail("not_object", "The query must be a JSON object.")
    if not spec:
        raise _fail("empty", "The query object is empty.")
    if _depth(spec, max_depth) > max_depth:
        raise _fail("too_deep", f"The query is nested more than {max_depth} levels deep.")
    return spec


def validate_mongo(text: str, params: list[Any], *, max_bytes: int = MAX_BYTES, max_depth: int = MAX_DEPTH) -> ValidatedMongoQuery:
    """A validated query with its parameters substituted, or ``MongoValidationError`` (which has a ``code``)."""
    spec = _parse(text, max_bytes, max_depth)
    is_find, is_aggregate = "find" in spec, "aggregate" in spec
    if is_find and is_aggregate:
        raise _fail("mixed_kind", "A query is either a find or an aggregate, not both.")
    if not (is_find or is_aggregate):
        raise _fail("unknown_key", 'A query must start with "find" or "aggregate" (other commands such as mapReduce are not allowed).')
    allowed = FIND_KEYS if is_find else AGGREGATE_KEYS
    unknown = sorted(set(spec) - allowed)
    if unknown:
        raise _fail("unknown_key", f"Unknown key(s) {', '.join(unknown)}. Allowed: {', '.join(sorted(allowed))}.")
    _scan(spec)

    kind = "find" if is_find else "aggregate"
    collection = _check_collection(spec[kind])
    collections = [collection]
    if is_find:
        if not isinstance(spec.get("filter", {}), dict):
            raise _fail("bad_filter", "filter must be an object.")
        sort = spec.get("sort")
        if sort is not None and (not isinstance(sort, dict) or not all(v in (1, -1) and not isinstance(v, bool) for v in sort.values())):
            raise _fail("bad_sort", "sort must be an object whose values are 1 or -1.")
        if spec.get("projection") is not None and not isinstance(spec["projection"], dict):
            raise _fail("bad_projection", "projection must be an object.")
        for name, cap in (("limit", MAX_LIMIT), ("skip", MAX_SKIP)):
            value = spec.get(name)
            minimum = 1 if name == "limit" else 0
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= cap):
                raise _fail("bad_limit", f"{name} must be a whole number from {minimum} to {cap:,}.")
    else:
        _check_pipeline(spec.get("pipeline"), collections)

    found: list[int] = []
    _placeholders(spec, found)
    count = max(found, default=0)
    if len(params) != count:
        raise _fail("param_mismatch", f"The query uses {count} parameter(s) but {len(params)} were supplied.")
    values = [_check_scalar(p, i) for i, p in enumerate(params, start=1)]

    ordered = {k: spec[k] for k in sorted(spec)}
    canonical = json.dumps(ordered, separators=(",", ":"), ensure_ascii=False)
    runnable = _substitute(spec, values)
    query = MongoQuery(
        kind=kind, collection=collection, filter=runnable.get("filter") or {}, sort=runnable.get("sort"),
        projection=runnable.get("projection"), limit=runnable.get("limit"), skip=runnable.get("skip"), pipeline=runnable.get("pipeline"),
    )
    shape_query = MongoQuery(
        kind=kind, collection=collection, filter=spec.get("filter") or {}, sort=spec.get("sort"), projection=spec.get("projection"),
        limit=spec.get("limit"), skip=spec.get("skip"), pipeline=spec.get("pipeline"),
    )
    shape = normalize_shape(shape_query)
    return ValidatedMongoQuery(
        query=query, canonical_json=canonical, fingerprint=hashlib.sha1(shape.encode()).hexdigest()[:16], shape=shape,
        collections=collections, param_count=count,
    )
