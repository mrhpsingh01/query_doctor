"""Export reviewable PostgreSQL index migrations in three formats."""
import hashlib
from pathlib import Path


def migration_sql(ddl):
    return ddl.replace('CREATE INDEX ON', 'CREATE INDEX CONCURRENTLY ON', 1).rstrip(';') + ';'


def export_migrations(directory: Path, stem: str, ddl: str):
    sql = migration_sql(ddl)
    paths = []
    raw = directory / f'{stem}.sql'
    raw.write_text(sql + '\n', encoding='utf-8')
    paths.append(raw)
    prisma = directory / f'{stem}-prisma' / 'migration.sql'
    prisma.parent.mkdir(parents=True, exist_ok=True)
    prisma.write_text('-- PostgreSQL: run without a transaction wrapper.\n' + sql + '\n', encoding='utf-8')
    paths.append(prisma)
    alembic = directory / f'{stem}-alembic.py'
    revision = hashlib.sha256(ddl.encode()).hexdigest()[:12]
    alembic.write_text(
        '"""Review down_revision and implement downgrade before applying."""\n'
        'from alembic import op\n\n'
        f'revision = {revision!r}\ndown_revision = None  # Set to your current migration head.\nbranch_labels = None\ndepends_on = None\n\n'
        'def upgrade():\n    with op.get_context().autocommit_block():\n'
        f'        op.execute({sql!r})\n\n'
        'def downgrade():\n    raise NotImplementedError("Set an explicit index name and review its rollback")\n', encoding='utf-8')
    paths.append(alembic)
    return paths
