import math

import pytest
from labdsn import BENCH_ADMIN_DSN, BENCH_SUPER_DSN

from querydoctor.benchmark import BenchmarkConfig
from querydoctor.benchmark.runner import _validate_ddl
from querydoctor.candidates import IndexCandidate, extract_usage, generate_candidates
from querydoctor.lab.query_templates import sample_instances
from querydoctor.ranking import FEATURE_NAMES
from querydoctor.ranking.dataset import DatasetRow, VariantEnv, build_rows, decoys, read_rows, write_rows
from querydoctor.schema import SchemaSnapshot, TableInfo

HEADLINE = "SELECT id, total_amount FROM orders WHERE customer_id = $1 AND created_at >= $2 ORDER BY created_at DESC"


def schema() -> SchemaSnapshot:
    def t(name, **cols):
        return TableInfo(name=name, row_estimate=10_000, columns=cols)

    return SchemaSnapshot(
        tables={
            "orders": t("orders", id="integer", customer_id="integer", total_amount="numeric", created_at="timestamp",
                        status="text", currency="text", shipped_at="timestamp"),
            "payments": t("payments", id="integer", order_id="integer", amount="numeric", method="text", paid_at="timestamp"),
        }
    )


def test_decoys_are_valid_plain_create_index_statements_that_differ_from_the_real_candidates():
    s = schema()
    found = decoys(extract_usage(HEADLINE, s), s)
    real = {c.ddl for c in generate_candidates(HEADLINE, s, [])}
    assert found and not ({d.ddl for d in found} & real)
    assert len({d.ddl for d in found}) == len(found)
    for d in found:
        assert isinstance(d, IndexCandidate) and d.id == IndexCandidate.make_id(d.ddl)
        assert _validate_ddl(d.ddl) == d.table == "orders"
        assert all(c.name in s.tables["orders"].columns for c in d.columns)
        assert d.rationale and "decoy" in d.rationale[0].lower()


def test_decoys_include_a_wrong_column_order_a_lone_leading_column_and_an_unrelated_column():
    s = schema()
    ddls = {d.ddl for d in decoys(extract_usage(HEADLINE, s), s)}
    assert "CREATE INDEX ON orders (created_at DESC, customer_id)" in ddls  # the key columns in the wrong order
    assert "CREATE INDEX ON orders (customer_id)" in ddls  # only the leading column of the good composite
    unrelated = [d for d in ddls if all(c not in d for c in ("customer_id", "created_at", "total_amount"))]
    assert len(unrelated) == 1 and unrelated[0].startswith("CREATE INDEX ON orders (")  # a column the query never touches


def test_a_single_column_key_has_no_wrong_order_or_lone_leading_decoy():
    sql = "SELECT id FROM payments WHERE order_id = $1"
    s = schema()
    ddls = {d.ddl for d in decoys(extract_usage(sql, s), s)}
    assert all("order_id" not in d for d in ddls) and len(ddls) == 1  # just the unrelated-column decoy


def test_a_table_the_schema_does_not_know_gets_no_decoys():
    assert decoys(extract_usage("SELECT id FROM nowhere WHERE a = $1", schema()), schema()) == []


def row(**over) -> DatasetRow:
    base = dict(
        features=dict.fromkeys(FEATURE_NAMES, 0.5), log_speedup=1.2, improvement_pct=70.0, useful=True, regression=False,
        index_size_mb=3.5, build_ms=120.0, group_id="g1", query_id="g1:tiny:abc", variant="tiny", template_id="t",
        ddl="CREATE INDEX ON orders (customer_id)", baseline_ms=4.0, candidate_ms=1.2, planner_score=0.8, uses_candidate=True,
        index_used=True, checksum_match=True, is_decoy=False, feature_version=1,
    )
    return DatasetRow(**{**base, **over})


def test_jsonl_round_trip_and_append(tmp_path):
    path = tmp_path / "rows.jsonl"
    first, second = row(), row(query_id="g2:tiny:def", group_id="g2", useful=False, improvement_pct=-5.0)
    write_rows(path, [first])
    write_rows(path, [second], append=True)
    assert read_rows(path) == [first, second]
    write_rows(path, [second])  # overwrite by default
    assert read_rows(path) == [second]
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_reading_a_missing_file_gives_no_rows(tmp_path):
    assert read_rows(tmp_path / "nope.jsonl") == []


@pytest.mark.integration
def test_building_rows_end_to_end_on_the_tiny_variant():
    from querydoctor.lab.variants import ensure_variant

    template = ensure_variant(BENCH_SUPER_DSN, "tiny")
    wanted = {"orders_by_customer_recent", "payments_by_order"}
    instances = [i for i in sample_instances("tiny", 2, seed=5) if i.template_id in wanted]
    assert len(instances) == 4
    env = VariantEnv(bench_dsn=BENCH_ADMIN_DSN, variant="tiny", template=template)
    rows = list(build_rows(env, instances, BenchmarkConfig(runs=5, warmups=1, timeout_s=300)))

    assert rows and {r.template_id for r in rows} == wanted
    for r in rows:
        assert list(r.features) == FEATURE_NAMES and all(math.isfinite(v) for v in r.features.values())
        assert all(math.isfinite(x) for x in (r.log_speedup, r.improvement_pct, r.index_size_mb, r.build_ms, r.baseline_ms, r.candidate_ms))
        assert r.useful == (r.improvement_pct >= 20 and r.checksum_match)
        assert r.regression == (r.log_speedup < math.log(0.9))
        assert r.group_id == r.query_id.split(":")[0] and r.query_id.split(":")[1] == "tiny" and r.variant == "tiny"
        assert r.features["d_has_plan"] == 1 or r.planner_score == 0.0
    assert any(r.useful for r in rows)  # an index on customer_id or order_id really helps
    decoy_rows = [r for r in rows if r.is_decoy]
    assert decoy_rows
    # a decoy on a column the query never touches cannot help; a lone leading column of a good composite may well do
    unrelated = [r for r in decoy_rows if not any(c in r.ddl for c in ("customer_id", "created_at", "order_id"))]
    assert unrelated
    for r in unrelated:
        assert r.uses_candidate is False or r.improvement_pct < 20
    # every candidate of a concrete query shares one baseline measurement
    by_query = {}
    for r in rows:
        by_query.setdefault(r.query_id, set()).add(r.baseline_ms)
    assert all(len(v) == 1 for v in by_query.values())
    assert len({r.query_id for r in rows}) >= 3


def test_pending_instances_skip_what_the_output_file_already_has():
    from querydoctor.lab.dataset_gen import pending_instances

    everything = pending_instances("tiny", 2, 7, set())
    done = {i.query_id for i in everything[:5]}
    rest = pending_instances("tiny", 2, 7, done)
    assert len(rest) == len(everything) - 5 and not ({i.query_id for i in rest} & done)
