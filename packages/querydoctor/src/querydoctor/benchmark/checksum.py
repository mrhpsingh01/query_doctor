"""A server-side fingerprint of a query's output, so original and indexed runs can be proven equivalent without any
business row ever leaving the database."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from querydoctor.db.errors import ExplainFailed, ExplainTimeout
from querydoctor.db.protocol import execute_single


def has_order_by(sql: str) -> bool:
    """True when the query's own result order is part of its meaning (a top-level ORDER BY)."""
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except (SqlglotError, ValueError):
        return False
    return isinstance(tree, exp.Query) and bool(tree.args.get("order"))


def result_checksum(conn: psycopg.Connection, sql: str, params: Sequence[Any], *, ordered: bool) -> str:
    """Count plus SHA-256 of fixed-width row digests; duplicates remain significant."""
    order = "" if ordered else " ORDER BY row_digest"
    aggregate = f"string_agg(row_digest, ''{order})"
    wrapped = (
        "SELECT count(*)::text || ':' || encode(sha256(convert_to(coalesce("
        f"{aggregate}, ''), 'UTF8')), 'hex') FROM ("
        "SELECT encode(sha256(convert_to(t::text, 'UTF8')), 'hex') AS row_digest "
        f"FROM ({sql}) AS t) AS digests"
    )
    try:
        return execute_single(conn, wrapped, params).get_value(0, 0).decode()
    except psycopg.errors.QueryCanceled as exc:
        raise ExplainTimeout("the result checksum exceeded the session timeout") from exc
    except psycopg.Error as exc:
        raise ExplainFailed(exc.diag.message_primary or str(exc), exc.sqlstate) from exc
    finally:
        conn.rollback()
