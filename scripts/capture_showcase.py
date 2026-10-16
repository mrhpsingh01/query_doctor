"""Capture reproducible showcase evidence against disposable PostgreSQL/MongoDB instances.
Set QD_CI_DSN (non-superuser CREATEDB, HypoPG in template1) and QD_SHOWCASE_MONGO_URI.
Creates only disposable databases. Never use production credentials.
"""
import json
import os
import uuid
from functools import wraps
from psycopg import sql as psql
from datetime import UTC, datetime
from pathlib import Path
import psycopg
from pymongo import MongoClient
from querydoctor.adapters.postgres import PostgresAdapter
from querydoctor.adapters.mongo import MongoAdapter
from querydoctor.benchmark.models import BenchmarkConfig
from querydoctor.benchmark.runner import run_benchmark
from querydoctor.benchmark.reliability import awards_validation
from querydoctor.benchmark.sandbox import Sandbox
from querydoctor.candidates.models import IndexCandidate, IndexColumn
from querydoctor.db import guarded_connection
from querydoctor.diagnosis import Thresholds

OUTPUT = Path(__file__).resolve().parents[1] / 'apps/web/lib/showcaseCaptured.json'


def record(adapter, connection, sql, ddl, table, columns):
    vq = adapter.validate_query(sql, [], max_chars=20000)
    raw = adapter.explain(connection, vq, [], execute=True)
    plan = adapter.normalize_plan(raw)
    schema = adapter.collect_schema(connection, vq)
    findings = adapter.diagnose(plan, schema, vq, Thresholds())
    idea = IndexCandidate(id=IndexCandidate.make_id(ddl), table=table, ddl=ddl, columns=[IndexColumn(name=c) for c in columns], rationale=['Showcase candidate derived from filter/join/order columns.'])
    planner = adapter.normalize_plan(adapter.explain(connection, vq, [], execute=False))
    tested = adapter.test_candidates(connection, vq, [], planner, [idea], schema)[0]
    return vq, idea, dict(sql=sql, raw_plan=raw, plan=plan.model_dump(mode='json'), findings=[dict(id=i+1, **f.model_dump(mode='json')) for i, f in enumerate(findings)], comparison=tested.comparison.model_dump(mode='json') if tested.comparison else None)


def isolated_work_mem(function):
    @wraps(function)
    def wrapped():
        dsn = os.environ['QD_CI_DSN']
        with psycopg.connect(dsn, autocommit=True) as conn:
            config = conn.execute("SELECT rolconfig FROM pg_roles WHERE rolname=current_user").fetchone()[0] or []
            old = next((item.split('=', 1)[1] for item in config if item.startswith('work_mem=')), None)
            conn.execute("ALTER ROLE CURRENT_USER SET work_mem = '64kB'")
        try:
            return function()
        finally:
            with psycopg.connect(dsn, autocommit=True) as conn:
                if old is None:
                    conn.execute('ALTER ROLE CURRENT_USER RESET work_mem')
                else:
                    conn.execute(psql.SQL('ALTER ROLE CURRENT_USER SET work_mem = {}').format(psql.Literal(old)))
    return wrapped


@isolated_work_mem
def main():
    dsn = os.environ['QD_CI_DSN']
    records = {}
    definitions = [
        ('scan', "CREATE TABLE orders AS SELECT n AS id, CASE WHEN n % 100 < 3 THEN 1 ELSE n % 33 + 2 END AS customer_id, TIMESTAMP '2022-01-01' + n * INTERVAL '1 second' AS created_at FROM generate_series(1,250000) n; ANALYZE orders;", "SELECT id, customer_id, created_at FROM orders WHERE customer_id = 1 ORDER BY created_at LIMIT 100", 'CREATE INDEX ON orders (customer_id, created_at)', ['customer_id', 'created_at']),
        ('sort', "CREATE TABLE orders AS SELECT n AS id, n % 1000 AS customer_id, TIMESTAMP '2022-01-01' + n * INTERVAL '1 second' AS created_at FROM generate_series(1,250000) n; ANALYZE orders;", "SELECT id, customer_id, created_at FROM orders ORDER BY created_at LIMIT 15000", 'CREATE INDEX ON orders (created_at)', ['created_at']),
        ('rejected', 'CREATE TABLE orders AS SELECT n AS id, n AS customer_id FROM generate_series(1,10000) n; ANALYZE orders; UPDATE orders SET customer_id = 1;', 'SELECT id FROM orders WHERE customer_id = 1', 'CREATE INDEX ON orders (customer_id)', ['customer_id']),
    ]
    adapter = PostgresAdapter()
    for key, seed, sql, ddl, columns in definitions:
        with Sandbox(dsn, template='template1') as template:
            with psycopg.connect(template.dsn, autocommit=True) as conn:
                conn.execute(seed)
            with guarded_connection(template.dsn, role_kind='bench_admin') as conn:
                _, _, capture = record(adapter, conn, sql, ddl, 'orders', columns)
            result = run_benchmark(dsn, sql, [], ddl, BenchmarkConfig(runs=7, warmups=3), template=template.name)
            capture['seed_sql'] = seed
            capture['benchmark'] = result.model_dump(mode='json')
            capture['validated'] = awards_validation(result)
            capture['captured_at'] = datetime.now(UTC).isoformat()
            capture['checksum_method'] = 'PostgreSQL row count plus SHA-256 of sorted SHA-256 row digests'
            records[key] = capture
            print(key, result.before.median_ms, result.after.median_ms, capture['validated'], flush=True)
    uri = os.environ['QD_SHOWCASE_MONGO_URI']
    with MongoClient(uri, serverSelectionTimeoutMS=5000) as client:
        source = 'qd_showcase_' + uuid.uuid4().hex[:12]
        try:
            db = client[source]
            db.customers.insert_many([{'_id': n} for n in range(20)])
            for start in range(0, 100000, 10000):
                db.orders.insert_many([{'_id': n, 'customer_id': n % 1000} for n in range(start, start+10000)])
            adapter = MongoAdapter(bench_uri=uri, source_db=source)
            sql = json.dumps({'aggregate': 'customers', 'pipeline': [{'$lookup': {'from': 'orders', 'localField': '_id', 'foreignField': 'customer_id', 'as': 'orders'}}, {'$project': {'_id': 1, 'order_count': {'$size': '$orders'}}}]})
            vq, idea, capture = record(adapter, db, sql, 'db.orders.createIndex({"customer_id":1})', 'orders', ['customer_id'])
            result = adapter.benchmark(uri, vq, [], idea, BenchmarkConfig(runs=7, warmups=3), on_progress=lambda _: None, cancelled=lambda: False)
            capture.update(benchmark=result.model_dump(mode='json'), validated=awards_validation(result), captured_at=datetime.now(UTC).isoformat(), checksum_method='MongoDB document count plus SHA-256 of sorted server-side SHA-256 document digests', seed_description='20 customers; 100000 orders; customer_id = order id modulo 1000')
            records['lookup'] = capture
            print('lookup', result.before.median_ms, result.after.median_ms, capture['validated'], flush=True)
        finally:
            client.drop_database(source)
    OUTPUT.write_text(json.dumps(records, indent=2, default=str) + '\n', encoding='utf-8')
    print(OUTPUT)


if __name__ == '__main__':
    main()
