"""Run exactly one statement through PostgreSQL's extended protocol.

Parse/Bind/Execute accepts a single statement only - PostgreSQL itself refuses "SELECT 1; SELECT 2" - so what is run can
never be more than one statement, whatever the text contains (the validator is a first line of defence, not the only
one). Parameters are bound as real protocol parameters: they are never quoted or interpolated into the SQL.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg import errors, pq

from querydoctor.db.errors import ParamMismatch
from querydoctor.db.params import check_params, to_wire

_OK = (pq.ExecStatus.TUPLES_OK, pq.ExecStatus.COMMAND_OK)


def execute_single(conn: psycopg.Connection, sql: str, params: Sequence[Any] = ()) -> pq.abc.PGresult:
    """Execute one statement with ``$1..$n`` parameters inside the connection's current transaction.

    Raises the matching ``psycopg.errors`` exception for any server error (QueryCanceled, SyntaxError, ...) and
    ``ParamMismatch`` when the number of parameters does not fit the statement.
    """
    check_params(params)
    if conn.info.transaction_status == pq.TransactionStatus.IDLE:
        conn.execute("SELECT 1")  # lets psycopg open the transaction, so rollback() undoes everything that follows
    pg = conn.pgconn
    encoding = conn.info.encoding

    prepared = pg.prepare(b"", sql.encode(encoding))
    if prepared.status not in _OK:
        raise errors.error_from_result(prepared, encoding=encoding)
    expected = pg.describe_prepared(b"").nparams
    if expected != len(params):
        raise ParamMismatch(f"query expects {expected} parameter(s) but {len(params)} were supplied")

    result = pg.exec_prepared(b"", [to_wire(p) for p in params])
    if result.status not in _OK:
        raise errors.error_from_result(result, encoding=encoding)
    return result
