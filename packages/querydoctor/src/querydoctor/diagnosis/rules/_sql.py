"""Parse the condition strings PostgreSQL prints in plans (e.g. ``((id)::text = '5'::text)``) with SQLGlot."""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

_FUNCTION_NAMES = {"timestamp_trunc": "date_trunc", "cast": "cast", "try_cast": "cast"}


def parse_condition(text: str | None) -> exp.Expression | None:
    if not text:
        return None
    try:
        return sqlglot.parse_one(text, read="postgres")
    except (SqlglotError, ValueError):
        return None


def column_names(text: str | None) -> list[str]:
    """Distinct column names referenced by a plan condition, in order of appearance."""
    tree = parse_condition(text)
    if tree is None:
        return []
    seen: list[str] = []
    for column in tree.find_all(exp.Column):
        if column.name not in seen:
            seen.append(column.name)
    return seen


def functions_on_columns(text: str | None) -> list[tuple[str, str]]:
    """``(function, column)`` pairs where a function or cast is applied to a column (not to a literal)."""
    tree = parse_condition(text)
    if tree is None:
        return []
    found: list[tuple[str, str]] = []
    for node in tree.find_all(exp.Func):
        if isinstance(node, (exp.Binary, exp.Connector, exp.Unary, exp.Paren)):
            continue
        column = node.find(exp.Column)
        if column is None:
            continue
        if isinstance(node, exp.Anonymous):
            name = str(node.name).lower()
        else:
            name = node.sql_name().lower()
        pair = (_FUNCTION_NAMES.get(name, name), column.name)
        if pair not in found:
            found.append(pair)
    return found
