from app.models import (
    Analysis,
    AuditEvent,
    BenchmarkRun,
    Connection,
    Finding,
    Project,
    QuerySnapshot,
    RecommendationCandidate,
)


def test_analysis_graph_round_trips(db_session):
    project = Project(name="Sample shop")
    db_session.add(project)
    db_session.flush()
    snapshot = QuerySnapshot(
        project_id=project.id, sql="SELECT 1", normalized_sql="SELECT ?", fingerprint="abc", params=[1, "x"]
    )
    db_session.add(snapshot)
    db_session.flush()
    analysis = Analysis(project_id=project.id, snapshot_id=snapshot.id, status="queued", execute=False)
    analysis.findings.append(
        Finding(
            rule_id="PG_SEQ_SCAN_FILTER_WASTE", severity="critical", title="t", summary="s",
            evidence={"filter_waste": 0.99}, node_ids=["0.0"], confidence=0.9, next_actions=["index it"],
        )
    )
    analysis.candidates.append(
        RecommendationCandidate(
            candidate_key="k1", table_name="orders", ddl="CREATE INDEX ON orders (customer_id)",
            columns=[{"name": "customer_id", "desc": False}], include=[], rationale=["equality"],
            source_rule_ids=["PG_SEQ_SCAN_FILTER_WASTE"], validation="suggested",
        )
    )
    db_session.add(analysis)
    db_session.commit()

    loaded = db_session.get(Analysis, analysis.id)
    assert loaded.status == "queued" and loaded.cancel_requested is False
    assert loaded.findings[0].evidence == {"filter_waste": 0.99}
    assert loaded.candidates[0].validation == "suggested"
    assert loaded.snapshot.params == [1, "x"]

    run = BenchmarkRun(analysis_id=analysis.id, candidate_id=loaded.candidates[0].id, status="queued", config={"runs": 7})
    db_session.add_all([run, AuditEvent(analysis_id=analysis.id, event_type="benchmark_approved", detail={"c": 1})])
    db_session.commit()
    assert db_session.get(BenchmarkRun, run.id).config == {"runs": 7}


def test_connection_stores_only_encrypted_password(db_session):
    project = Project(name="p")
    db_session.add(project)
    db_session.flush()
    conn = Connection(
        project_id=project.id, name="c", kind="sample", host="h", port=5432, database="d", username="u",
        encrypted_password="ciphertext",
    )
    db_session.add(conn)
    db_session.commit()
    assert not hasattr(conn, "password")
