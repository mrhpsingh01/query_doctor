import json
import re

import psycopg
import pytest
from labdsn import TARGET_RO_DSN

from querydoctor.db import explain
from querydoctor.lab.query_templates import TEMPLATES, QueryInstance, sample_instances
from querydoctor.safety import validate_query

TABLES = ["orders", "events", "payments", "order_items", "customers", "products"]


def test_there_are_at_least_thirty_templates_with_unique_ids_over_every_table():
    ids = [t.id for t in TEMPLATES]
    assert len(TEMPLATES) >= 30 and len(set(ids)) == len(ids)
    used = {name for t in TEMPLATES for name in validate_query(t.sql).tables}
    assert set(TABLES) <= used


def test_every_template_validates_and_has_a_unique_fingerprint():
    fingerprints = {}
    for t in TEMPLATES:
        vq = validate_query(t.sql)  # one SELECT, nothing dangerous
        assert vq.fingerprint not in fingerprints, f"{t.id} duplicates {fingerprints.get(vq.fingerprint)}"
        fingerprints[vq.fingerprint] = t.id


def test_the_templates_cover_the_query_shapes_the_ranker_must_see():
    sql = " ".join(t.sql.lower() for t in TEMPLATES)
    for shape, pattern in {
        "equality": r"\w = \$", "range": r">= \$|< \$|> \$", "join": r" join ", "sort": r"order by", "group": r"group by",
        "subquery": r"\(select|exists \(select", "expression": r"lower\(|date_trunc\(|extract\(", "in-list": r" in \(\$",
        "limit": r" limit ",
    }.items():
        assert re.search(pattern, sql), f"no template with a {shape}"


def test_instances_are_deterministic_and_vary_with_the_seed_and_variant():
    a = sample_instances("tiny", 3, seed=1)
    assert a == sample_instances("tiny", 3, seed=1)
    assert a != sample_instances("tiny", 3, seed=2)
    assert [i.params for i in a] != [i.params for i in sample_instances("small", 3, seed=1)]


def test_each_instance_binds_exactly_the_templates_parameters_with_plain_values():
    instances = sample_instances("tiny", 4, seed=7)
    assert {i.template_id for i in instances} == {t.id for t in TEMPLATES}
    for i in instances:
        assert isinstance(i, QueryInstance)
        assert validate_query(i.sql).param_count == len(i.params), i.template_id
        assert all(isinstance(p, (int, float, str)) and not isinstance(p, bool) for p in i.params)
        json.dumps(i.params)


def test_n_per_template_instances_are_distinct_and_ids_group_by_fingerprint_variant_and_parameters():
    instances = sample_instances("tiny", 5, seed=3)
    by_template = {}
    for i in instances:
        by_template.setdefault(i.template_id, []).append(i)
    assert all(len({tuple(map(str, x.params)) for x in group}) == len(group) for group in by_template.values() if len(group) > 1)
    assert len({i.query_id for i in instances}) == len(instances)
    for i in instances:
        assert i.query_id.startswith(f"{i.fingerprint}:tiny:")
        assert i.fingerprint == validate_query(i.sql).fingerprint


@pytest.mark.integration
def test_every_template_can_be_planned_on_the_small_variant():
    with psycopg.connect(TARGET_RO_DSN) as conn:  # the target database holds the small variant's data
        for i in sample_instances("small", 1, seed=11):
            plan = explain(conn, i.sql, i.params, execute=False)
            assert plan[0]["Plan"], i.template_id
