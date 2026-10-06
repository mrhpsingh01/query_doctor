from __future__ import annotations

import datetime as dt
import decimal
import math
from collections.abc import Sequence
from typing import Any

from querydoctor.db.errors import ParamMismatch

_SCALARS = (int, float, str, bool, type(None), decimal.Decimal, dt.date, dt.datetime, dt.time)


def check_params(params: Sequence[Any]) -> None:
    """Parameters must be plain scalars PostgreSQL can accept: no containers, no NaN/Infinity, no NUL characters."""
    for i, value in enumerate(params, start=1):
        if not isinstance(value, _SCALARS):
            raise ParamMismatch(f"parameter ${i} has unsupported type {type(value).__name__}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ParamMismatch(f"parameter ${i} is not a finite number")
        if isinstance(value, str) and "\x00" in value:
            raise ParamMismatch(f"parameter ${i} contains a NUL character")


def to_wire(value: Any) -> bytes | None:
    """Text form of a parameter for the extended protocol; PostgreSQL infers each parameter's type from the query."""
    if value is None:
        return None
    if isinstance(value, bool):
        return b"true" if value else b"false"
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat().encode()
    return str(value).encode()
