from __future__ import annotations

import datetime as dt
import decimal
from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg import sql as psql

from querydoctor.db.errors import ParamMismatch

_SCALARS = (int, float, str, bool, type(None), decimal.Decimal, dt.date, dt.datetime, dt.time)


def check_params(params: Sequence[Any]) -> None:
    for i, value in enumerate(params, start=1):
        if not isinstance(value, _SCALARS):
            raise ParamMismatch(f"parameter ${i} has unsupported type {type(value).__name__}")


def literal_args(conn: psycopg.Connection, params: Sequence[Any]) -> str:
    """``(1, 'x', ...)`` for ``EXECUTE stmt(...)``. EXECUTE arguments cannot be bound as protocol parameters, so values
    are rendered with psycopg's literal quoting after a type whitelist - never string-formatted by hand."""
    check_params(params)
    if not params:
        return ""
    return "(" + ", ".join(psql.Literal(p).as_string(conn) for p in params) + ")"
