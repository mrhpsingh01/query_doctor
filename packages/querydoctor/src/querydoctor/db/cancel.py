from __future__ import annotations

import psycopg


def cancel_backend(dsn: str, pid: int) -> bool:
    """Ask PostgreSQL to cancel the statement running in backend ``pid`` (same-role sessions only)."""
    with psycopg.connect(dsn, connect_timeout=3, autocommit=True) as conn:
        row = conn.execute("SELECT pg_cancel_backend(%s)", (pid,)).fetchone()
    return bool(row and row[0])
