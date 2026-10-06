"""Static validation of user-supplied SQL. Nothing reaches a database before passing through here."""

from __future__ import annotations

import hashlib

import sqlglot
from pydantic import BaseModel
from sqlglot import exp
from sqlglot.errors import SqlglotError

DENIED_FUNCTIONS = frozenset(
    {
        "pg_sleep", "pg_sleep_for", "pg_sleep_until", "set_config", "nextval", "setval",
        "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "pg_rotate_logfile",
        "pg_stat_file", "query_to_xml", "query_to_xml_and_xmlschema", "cursor_to_xml",
        "table_to_xml", "schema_to_xml", "database_to_xml", "txid_current",
    }
)
DENIED_PREFIXES = (
    "lo_", "dblink", "pg_advisory", "pg_try_advisory", "pg_read", "pg_ls", "pg_file", "pg_logical", "pg_create", "pg_drop",
    "pg_notify",
)


class ValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ValidatedQuery(BaseModel):
    sql: str
    normalized_sql: str
    fingerprint: str
    tables: list[str]
    param_count: int
    has_select_star: bool


def _function_names(tree: exp.Expression) -> set[str]:
    names: set[str] = set()
    for node in tree.find_all(exp.Func):
        if isinstance(node, exp.Anonymous):
            names.add(str(node.name).lower())
        else:
            names.add(node.sql_name().lower())
    return names


def _check_functions(tree: exp.Expression) -> None:
    for name in _function_names(tree):
        if name in DENIED_FUNCTIONS or name.startswith(DENIED_PREFIXES):
            raise ValidationError("denied_function", f"function {name}() is not allowed")


def _check_select_only(root: exp.Expression) -> None:
    if isinstance(root, exp.Command) and str(root.this).upper() == "EXPLAIN":
        raise ValidationError(
            "explain_not_allowed",
            "Paste the bare SELECT; QueryDoctor runs EXPLAIN itself.",
        )
    if not isinstance(root, exp.Query):
        raise ValidationError("not_select", "only SELECT statements are supported")
    if root.find(exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Command):
        raise ValidationError("not_select", "data-modifying statements are not allowed")
    for select in root.find_all(exp.Select):
        if select.args.get("into"):
            raise ValidationError("not_select", "SELECT ... INTO is not allowed")
        if select.args.get("locks"):
            raise ValidationError("not_select", "row-locking clauses (FOR UPDATE/SHARE) are not allowed")


def _normalize(root: exp.Expression) -> str:
    def literal_to_marker(node: exp.Expression) -> exp.Expression:
        if isinstance(node, (exp.Literal, exp.Parameter)):
            return exp.Var(this="?")
        return node

    return root.copy().transform(literal_to_marker).sql(dialect="postgres")


def _tables(root: exp.Expression) -> list[str]:
    ctes = {str(cte.alias).lower() for cte in root.find_all(exp.CTE)}
    names = {
        t.name for t in root.find_all(exp.Table) if isinstance(t.this, exp.Identifier) and t.name.lower() not in ctes
    }
    return sorted(names)


def _param_count(root: exp.Expression) -> int:
    indexes = []
    for p in root.find_all(exp.Parameter):
        try:
            indexes.append(int(p.this.name))
        except (ValueError, AttributeError):
            continue
    return max(indexes, default=0)


def _has_select_star(root: exp.Expression) -> bool:
    for select in root.find_all(exp.Select):
        for projection in select.expressions:
            if isinstance(projection, exp.Star) or (
                isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)
            ):
                return True
    return False


def _require_stable_round_trip(root: exp.Expression, canonical: str) -> None:
    """The canonical text is what gets executed, so it must mean to PostgreSQL what the parsed tree means to us.
    SQLGlot occasionally prints a construct in a form that reads differently (e.g. an E-string with an escaped backslash
    becomes an unterminated string). Anything that does not parse back to the identical tree is refused."""
    try:
        again = [s for s in sqlglot.parse(canonical, read="postgres") if s is not None]
    except SqlglotError:
        again = []
    if len(again) != 1 or again[0] != root:
        raise ValidationError(
            "unsupported_syntax",
            "this query uses syntax QueryDoctor cannot safely re-write; simplify string escapes or literals and try again",
        )


def validate_query(sql: str, *, max_chars: int = 20_000) -> ValidatedQuery:
    if "\x00" in sql:
        raise ValidationError("invalid_character", "the query contains a NUL character, which PostgreSQL cannot accept")
    if len(sql) > max_chars:
        raise ValidationError("too_long", f"query exceeds {max_chars} characters")
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except SqlglotError as exc:
        raise ValidationError("parse_error", f"could not parse SQL: {exc}") from exc
    if not statements:
        raise ValidationError("parse_error", "query is empty")
    if len(statements) > 1:
        raise ValidationError("multiple_statements", "exactly one statement is allowed")

    root = statements[0]
    _check_select_only(root)
    _check_functions(root)

    canonical = root.sql(dialect="postgres")
    _require_stable_round_trip(root, canonical)

    normalized = _normalize(root)
    return ValidatedQuery(
        sql=canonical,
        normalized_sql=normalized,
        fingerprint=hashlib.sha1(normalized.encode()).hexdigest()[:16],
        tables=_tables(root),
        param_count=_param_count(root),
        has_select_star=_has_select_star(root),
    )
