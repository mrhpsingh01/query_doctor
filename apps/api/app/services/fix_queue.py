"""Batch orchestration and evidence selection for the ranked workload fix queue."""
from sqlalchemy import select
from querydoctor.safety import validate_query
from querydoctor.workload.fix_queue import rank_fixes
from app.models import Analysis, AuditEvent, BenchmarkRun, QuerySnapshot, WorkloadSnapshot, Project
from app.services import workload
from app.services.pipeline import run_analysis_pipeline
from app.services.benchmarks import run_benchmark_job


def queue_for_snapshot(session, snapshot, queries):
    analyses = session.scalars(select(Analysis).where(
        Analysis.project_id == snapshot.project_id, Analysis.engine == snapshot.engine,
        Analysis.connection_id == snapshot.source_connection_id,
    ).order_by(Analysis.id.desc())).all()
    seen, evidence = set(), []
    for analysis in analyses:
        fingerprint = analysis.snapshot.fingerprint
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        if analysis.status != 'completed':
            continue
        for candidate in analysis.candidates:
            if candidate.dropped or candidate.validation != 'benchmark_validated':
                continue
            run = session.scalar(select(BenchmarkRun).where(BenchmarkRun.candidate_id == candidate.id).order_by(BenchmarkRun.id.desc()).limit(1))
            if run and run.status == 'completed' and run.result:
                evidence.append({'fingerprint': fingerprint, 'analysis_id': analysis.id, 'ddl': candidate.ddl, 'result': run.result})
    seconds = (snapshot.capture_end - snapshot.capture_start).total_seconds() if snapshot.capture_start and snapshot.capture_end else None
    return rank_fixes(queries, evidence, seconds)


def prepare_batch(session, snapshot: WorkloadSnapshot, top_n: int, scale: str):
    if snapshot.source_connection_id is not None or snapshot.engine != 'postgres':
        raise ValueError('Automatic batch benchmarks currently require the PostgreSQL sample source; customer data needs an explicitly seeded isolated CI database.')
    # Serialize concurrent approvals per project while checking active query shapes.
    session.execute(select(Project).where(Project.id == snapshot.project_id).with_for_update())
    queries = sorted(workload._load(session, snapshot), key=lambda q: -q.total_ms)[:top_n]
    ids, skipped = [], []
    for query in queries:
        sample = workload._catalog_sample(query.fingerprint, scale)
        if not sample or sample['engine'] != 'postgres':
            skipped.append({'fingerprint': query.fingerprint, 'reason': 'Bound parameters unavailable: add this query to a seeded CI workload.'})
            continue
        active = session.scalar(select(Analysis).join(QuerySnapshot).where(
            Analysis.project_id == snapshot.project_id, Analysis.connection_id.is_(None),
            Analysis.engine == 'postgres', QuerySnapshot.fingerprint == query.fingerprint,
            Analysis.status.in_(['queued', 'running'])))
        if active:
            skipped.append({'fingerprint': query.fingerprint, 'reason': 'Analysis already queued or running.'})
            continue
        vq = validate_query(sample['sql'])
        row = Analysis(project_id=snapshot.project_id, engine='postgres', execute=True, status='queued', snapshot=QuerySnapshot(
            project_id=snapshot.project_id, engine='postgres', sql=vq.sql, normalized_sql=vq.normalized_sql,
            fingerprint=vq.fingerprint, params=sample['params']))
        session.add(row)
        session.flush()
        session.add(AuditEvent(analysis_id=row.id, event_type='workload_batch_approved', detail={'snapshot_id': snapshot.id}))
        ids.append(row.id)
    session.commit()
    return {'analysis_ids': ids, 'skipped': skipped}


def execute_batch(session, ids, pipeline_deps, benchmark_deps, enqueue_benchmark=None):
    for analysis_id in ids:
        analysis = session.get(Analysis, analysis_id)
        if analysis is None or analysis.connection_id is not None or analysis.engine != 'postgres':
            continue
        run_analysis_pipeline(session, analysis_id, pipeline_deps)
        session.refresh(analysis)
        if analysis.status != 'completed':
            continue
        for candidate in sorted(analysis.candidates, key=lambda c: c.rank)[:3]:
            if candidate.dropped or candidate.validation != 'planner_validated':
                continue
            if session.scalar(select(BenchmarkRun).where(BenchmarkRun.candidate_id == candidate.id)):
                continue
            run = BenchmarkRun(analysis_id=analysis.id, candidate_id=candidate.id, status='queued', config={})
            session.add(run)
            session.flush()
            session.add(AuditEvent(analysis_id=analysis.id, event_type='benchmark_approved', detail={'run_id': run.id, 'source': 'workload_batch'}))
            session.commit()
            if enqueue_benchmark is None:
                run_benchmark_job(session, run.id, benchmark_deps)
            else:
                try:
                    enqueue_benchmark(run.id)
                except Exception:
                    run.status, run.error = 'failed', 'Benchmark could not be queued. Retry when the worker is available.'
                    session.commit()
