from __future__ import annotations

from querydoctor.candidates.models import IndexCandidate
from querydoctor.schema import IndexInfo, SchemaSnapshot

MIN_TABLE_ROWS = 1_000
MAX_KEY_COLUMNS_ALLOWED = 6


def _keys(c: IndexCandidate) -> list[str]:
    return [c.expression.lower()] if c.expression else [col.name for col in c.columns]


def _covers_include(include: list[str], keys: list[str], extra: list[str]) -> bool:
    return set(include) <= set(keys) | set(extra)


def _same_shape(predicate_a: str | None, expression_a: str | None, predicate_b: str | None, expression_b: str | None) -> bool:
    return predicate_a == predicate_b and (expression_a or "").lower() == (expression_b or "").lower()


def _existing_reason(c: IndexCandidate, idx: IndexInfo) -> str | None:
    keys = _keys(c)
    idx_keys = [k.lower() for k in idx.columns] if c.expression else idx.columns
    # A full (non-partial) existing index serves a partial candidate too; a partial one only serves the same predicate.
    if idx.predicate is not None and idx.predicate != c.predicate:
        return None
    if idx.unique and idx.predicate is None and idx_keys == keys:
        # Lookups on a unique key return at most one row; covering columns would not change that.
        return f"duplicate of existing index {idx.name} (a unique key: lookups return at most one row)"
    if not _covers_include(c.include, idx.columns, idx.include):
        return None
    if idx_keys == keys:
        return f"duplicate of existing index {idx.name}"
    if idx_keys[: len(keys)] == keys:
        return f"covered by existing index {idx.name} (its leading columns are the same)"
    return None


def prune_redundant(
    candidates: list[IndexCandidate], schema: SchemaSnapshot
) -> tuple[list[IndexCandidate], list[tuple[IndexCandidate, str]]]:
    """Drop candidates that would not earn their write cost. Returns ``(kept, [(dropped, reason)])``."""
    dropped: list[tuple[IndexCandidate, str]] = []
    alive: list[IndexCandidate] = []

    for c in candidates:
        info = schema.tables.get(c.table)
        reason: str | None = None
        if info is None:
            reason = f"unknown table {c.table}"
        elif info.row_estimate < MIN_TABLE_ROWS:
            reason = f"table too small to benefit ({info.row_estimate:,} rows)"
        elif len(c.columns) > MAX_KEY_COLUMNS_ALLOWED:
            reason = f"too many key columns ({len(c.columns)}): wide indexes cost more to write than they save"
        elif info.columns and any(
            name not in info.columns for name in [*(col.name for col in c.columns), *c.include]
        ):
            reason = "unknown column in the index definition"
        else:
            for idx in schema.indexes.get(c.table, []):
                reason = _existing_reason(c, idx)
                if reason:
                    break
        if reason:
            dropped.append((c, reason))
        else:
            alive.append(c)

    kept: list[IndexCandidate] = []
    for a in alive:
        keys_a = _keys(a)
        winner = next(
            (
                b for b in alive
                if b is not a and b.table == a.table and len(_keys(b)) > len(keys_a)
                and _keys(b)[: len(keys_a)] == keys_a
                and _same_shape(a.predicate, a.expression, b.predicate, b.expression)
                and _covers_include(a.include, _keys(b), b.include)
            ),
            None,
        )
        if winner is not None:
            dropped.append((a, f"covered by candidate {winner.ddl}"))
        else:
            kept.append(a)
    return kept, dropped
