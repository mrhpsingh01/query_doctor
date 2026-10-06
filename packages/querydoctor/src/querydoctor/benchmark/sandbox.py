"""Isolated copies of the sample database for benchmarking.

A sandbox is ``CREATE DATABASE bench_<id> TEMPLATE querydoctor_sample`` on the dedicated bench instance: a seconds-long
clone that can be indexed and timed freely and is always dropped afterwards. The template is never connected to by
benchmark work (PostgreSQL refuses to clone a database that has other sessions), so it cannot be modified.
"""

from __future__ import annotations

import re
import time
import uuid

import psycopg
from psycopg import sql as psql
from psycopg.conninfo import make_conninfo

NAME_PATTERN = r"^bench_[0-9a-f]{12}$"
_COMMENT_RE = re.compile(r"created=(\d+)")


class SandboxError(RuntimeError):
    pass


class Sandbox:
    def __init__(self, admin_dsn: str, template: str = "querydoctor_sample") -> None:
        self.admin_dsn = admin_dsn
        self.template = template
        self.name: str | None = None

    @property
    def dsn(self) -> str:
        if self.name is None:
            raise SandboxError("sandbox has not been created")
        return make_conninfo(self.admin_dsn, dbname=self.name)

    def create(self) -> str:
        name = f"bench_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(self.admin_dsn, autocommit=True, connect_timeout=5) as conn:
            for _attempt in range(10):
                try:
                    conn.execute(psql.SQL("CREATE DATABASE {} TEMPLATE {}").format(psql.Identifier(name), psql.Identifier(self.template)))
                    break
                except psycopg.errors.ObjectInUse:  # another clone is reading the template right now
                    time.sleep(0.3)
            else:
                raise SandboxError("could not clone the template database (it stayed busy)")
            self.name = name
            conn.execute(
                psql.SQL("COMMENT ON DATABASE {} IS {}").format(psql.Identifier(name), psql.Literal(f"qd_bench created={int(time.time())}"))
            )
        return name

    def drop(self) -> None:
        if self.name is None:
            return
        name, self.name = self.name, None
        _drop(self.admin_dsn, name)

    def __enter__(self) -> Sandbox:
        self.create()
        return self

    def __exit__(self, *exc) -> None:
        self.drop()


def _drop(admin_dsn: str, name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as conn:
        conn.execute(psql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(psql.Identifier(name)))


def drop_stale(admin_dsn: str, max_age_s: float) -> int:
    """Drop leftover sandboxes (crashed workers) at least ``max_age_s`` old. Returns how many were dropped."""
    dropped = 0
    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as conn:
        rows = conn.execute(
            "SELECT datname, shobj_description(oid, 'pg_database') FROM pg_database WHERE datname ~ %s", (NAME_PATTERN,)
        ).fetchall()
        for name, comment in rows:
            match = _COMMENT_RE.search(comment or "")
            created = int(match.group(1)) if match else 0  # unknown age: treat as old
            if time.time() - created >= max_age_s:
                conn.execute(psql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(psql.Identifier(name)))
                dropped += 1
    return dropped
