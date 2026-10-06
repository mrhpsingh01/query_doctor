from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

import psycopg

from querydoctor.db.errors import UnsafeRole


@contextmanager
def guarded_connection(
    dsn: str,
    *,
    role_kind: Literal["readonly", "bench_admin"] = "readonly",
    statement_timeout_ms: int = 10_000,
    lock_timeout_ms: int = 1_000,
) -> Iterator[psycopg.Connection]:
    """Open a target connection with QueryDoctor's session guards.

    Guards are passed as startup options so they survive rollbacks. ``readonly`` sessions additionally force
    ``default_transaction_read_only`` and refuse superuser roles; ``bench_admin`` (bench-postgres only) may write.
    """
    options = [
        f"-c statement_timeout={statement_timeout_ms}",
        f"-c lock_timeout={lock_timeout_ms}",
        "-c idle_in_transaction_session_timeout=15000",
    ]
    if role_kind == "readonly":
        options.append("-c default_transaction_read_only=on")
    conn = psycopg.connect(dsn, options=" ".join(options), connect_timeout=5, autocommit=False)
    try:
        is_super = conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()[0]
        if is_super:
            raise UnsafeRole("refusing to run analysis as a superuser role")
        conn.rollback()
        yield conn
    finally:
        conn.close()
