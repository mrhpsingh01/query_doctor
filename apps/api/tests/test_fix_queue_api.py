from datetime import UTC, datetime
from querydoctor.lab.catalog import build_catalog
from querydoctor.safety import validate_query
from app.models import Analysis, WorkloadQueryRow, WorkloadSnapshot


def seed_snapshot(client, *, engine='postgres'):
    with client.app.state.sessionmaker() as session:
        snapshot = WorkloadSnapshot(project_id=1, engine=engine, source='pg_stat_statements', query_count=1, skipped_count=0, capture_end=datetime.now(UTC))
        session.add(snapshot)
        session.flush()
        sample = build_catalog('small')[0]
        vq = validate_query(sample.sql)
        session.add(WorkloadQueryRow(snapshot_id=snapshot.id, fingerprint=vq.fingerprint, normalized_sql=vq.normalized_sql, sample_sql=sample.sql,
            calls=100, total_ms=1000, mean_ms=10, stddev_ms=0, min_ms=10, max_ms=10, rows=100, shared_hit_blocks=1, shared_read_blocks=0, temp_blocks=0, p95_est_ms=10, tables=vq.tables))
        session.commit()
        return snapshot.id


def test_batch_requires_approval_and_scopes_snapshot(client, monkeypatch):
    from app.workers.tasks import run_workload_batch
    queued=[]
    monkeypatch.setattr(run_workload_batch, 'delay', lambda ids: queued.extend(ids))
    snapshot_id=seed_snapshot(client)
    url='/api/projects/1/workload/batch'
    assert client.post(url, json={'snapshot_id':snapshot_id}).status_code == 422
    response=client.post(url, json={'snapshot_id':snapshot_id, 'approve_benchmarks':True})
    assert response.status_code == 202
    assert len(queued) == 1
    again=client.post(url, json={'snapshot_id':snapshot_id, 'approve_benchmarks':True}).json()
    assert again['analysis_ids'] == [] and again['skipped']
    assert client.post('/api/projects/999/workload/batch', json={'snapshot_id':snapshot_id, 'approve_benchmarks':True}).status_code == 404


def test_failed_dispatch_leaves_no_permanently_queued_analysis(client, monkeypatch):
    from app.workers.tasks import run_workload_batch
    def fail(ids): raise RuntimeError('broker unavailable')
    monkeypatch.setattr(run_workload_batch, 'delay', fail)
    snapshot_id=seed_snapshot(client)
    assert client.post('/api/projects/1/workload/batch', json={'snapshot_id':snapshot_id, 'approve_benchmarks':True}).status_code == 503
    with client.app.state.sessionmaker() as session:
        assert session.query(Analysis).one().status == 'failed'


def test_mongo_batch_is_explicitly_unsupported(client):
    snapshot_id=seed_snapshot(client, engine='mongodb')
    assert client.post('/api/projects/1/workload/batch', json={'snapshot_id':snapshot_id, 'approve_benchmarks':True}).status_code == 409

def test_postgres_capture_window_comes_from_statistics_reset(db_session, monkeypatch):
    from app.models import Project
    from app.services import workload
    from datetime import timedelta
    start=datetime.now(UTC)-timedelta(days=1)
    class Connection:
        def execute(self, sql):
            assert sql == 'SELECT stats_reset FROM pg_stat_statements_info'
            return self
        def fetchone(self): return (start,)
    monkeypatch.setattr(workload, 'read_statements', lambda conn, limit: [])
    project=Project(name='capture-window')
    db_session.add(project)
    db_session.commit()
    snapshot=workload.import_snapshot(db_session, Connection(), project.id)
    assert snapshot.capture_start.replace(tzinfo=UTC) == start
