"""Seeded template databases ("variants") on the bench instance, one per data shape the ranker's dataset is built from.

A variant is a complete copy of the sample schema, owned by ``qd_bench`` (so a clone's indexes can be created by the
benchmark role) and carrying the ``hypopg`` extension (so what-if plans can be produced in a clone). Variants are
templates: ``Sandbox(admin_dsn, template=variant_db_name(v))`` clones one in seconds.
"""

from __future__ import annotations

import time

import psycopg
from psycopg import sql as psql
from psycopg.conninfo import make_conninfo

from querydoctor.lab.generator import COUNTS
from querydoctor.lab.seed import seed

VARIANTS = ["tiny", "small", "medium", "skew_low", "skew_high"]
OWNER = "qd_bench"
OWNER_PASSWORD = "qd_bench"  # the bench instance is a throwaway local container (see infrastructure/compose.yaml)
_DONE = "qd_variant complete"


def variant_db_name(name: str) -> str:
    return f"qd_variant_{name}"


def _complete(conn: psycopg.Connection, db: str) -> bool:
    row = conn.execute("SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = %s", (db,)).fetchone()
    return row is not None and (row[0] or "").startswith(_DONE)


def ensure_variant(admin_dsn: str, name: str, *, log=lambda msg: None) -> str:
    """Create and seed ``qd_variant_<name>`` if it is missing (or was left half-built); return its name.

    ``admin_dsn`` must be a superuser connection (installing ``hypopg`` needs one). Idempotent.
    """
    if name not in VARIANTS or name not in COUNTS:
        raise ValueError(f"unknown variant {name!r}; expected one of {VARIANTS}")
    db = variant_db_name(name)
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,)).fetchone() is not None
        if exists and _complete(admin, db):
            return db
        if exists:  # an earlier run was interrupted: start over
            admin.execute(psql.SQL("DROP DATABASE {} WITH (FORCE)").format(psql.Identifier(db)))
        admin.execute(psql.SQL("CREATE DATABASE {} OWNER {}").format(psql.Identifier(db), psql.Identifier(OWNER)))
        with psycopg.connect(make_conninfo(admin_dsn, dbname=db), autocommit=True) as inside:
            inside.execute("CREATE EXTENSION IF NOT EXISTS hypopg")
        log(f"seeding variant {name}")
        owner_dsn = make_conninfo(admin_dsn, dbname=db, user=OWNER, password=OWNER_PASSWORD)
        seed(owner_dsn, name, log=log)
        admin.execute(
            psql.SQL("COMMENT ON DATABASE {} IS {}").format(psql.Identifier(db), psql.Literal(f"{_DONE} at={int(time.time())}"))
        )
    return db
