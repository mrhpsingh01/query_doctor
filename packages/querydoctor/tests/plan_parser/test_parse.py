import json

import pytest

from querydoctor.plan_parser import ParsedPlan, iter_nodes, parse_explain


def test_normalizes_tree_ids(load_plan):
    plan = parse_explain(load_plan("seqscan_filter_waste"))
    assert plan.root.node_id == "0"
    assert plan.root.parent_id is None
    scan = plan.root.children[0]
    assert scan.node_id == "0.0"
    assert scan.parent_id == "0"
    assert scan.node_type == "Seq Scan"
    assert scan.relation_name == "orders"
    assert scan.rows_removed_by_filter == 1813561
    assert scan.actual_rows == 10842
    assert scan.filter_condition == "(orders.customer_id = 4242)"
    assert plan.executed is True
    assert plan.execution_time_ms == pytest.approx(742.1)
    assert plan.settings["work_mem"] == "4MB"
    assert [n.node_id for n in iter_nodes(plan.root)] == ["0", "0.0"]


def test_filter_waste(load_plan):
    scan = parse_explain(load_plan("seqscan_filter_waste")).root.children[0]
    assert scan.filter_waste == pytest.approx(0.9941, abs=1e-4)


@pytest.mark.parametrize(
    "estimated,actual,expected",
    [(100, 10_000, 100.0), (10_000, 100, 100.0), (0, 0, 1.0), (0, 5, 5.0), (50, 50, 1.0)],
)
def test_error_ratio(load_plan, estimated, actual, expected):
    raw = load_plan("seqscan_filter_waste")
    node = raw[0]["Plan"]["Plans"][0]
    node["Plan Rows"], node["Actual Rows"] = estimated, actual
    assert parse_explain(raw).root.children[0].error_ratio == pytest.approx(expected)


def test_exclusive_time_multiplies_loops(load_plan):
    raw = load_plan("nested_loop_repeated")
    inner = raw[0]["Plan"]["Plans"][1]
    inner["Actual Total Time"], inner["Actual Loops"] = 0.5, 1000
    plan = parse_explain(raw)
    _outer_scan, inner_scan = plan.root.children
    assert inner_scan.exclusive_time_ms == pytest.approx(500.0)
    # root: 6200 total - (4.0*1 + 0.5*1000) children
    assert plan.root.exclusive_time_ms == pytest.approx(6200 - 4.0 - 500.0)


def test_exclusive_time_never_negative(load_plan):
    raw = load_plan("nested_loop_repeated")
    raw[0]["Plan"]["Actual Total Time"] = 1.0
    assert parse_explain(raw).root.exclusive_time_ms == 0.0


def test_explain_only_plan_has_no_actuals(load_plan):
    plan = parse_explain(load_plan("explain_only"))
    node = plan.root
    assert plan.executed is False
    assert node.actual_rows is None and node.actual_time_ms is None and node.loops is None
    assert node.filter_waste is None and node.error_ratio is None and node.exclusive_time_ms is None
    assert node.estimated_rows == 1_000_000


def test_never_executed_node_is_handled(load_plan):
    raw = load_plan("nested_loop_repeated")
    inner = raw[0]["Plan"]["Plans"][1]
    inner.update({"Actual Loops": 0, "Actual Rows": 0, "Actual Total Time": 0.0, "Actual Startup Time": 0.0})
    inner.pop("Rows Removed by Filter")
    node = parse_explain(raw).root.children[1]
    assert node.loops == 0
    assert node.exclusive_time_ms == 0.0
    assert node.error_ratio is None and node.filter_waste is None


def test_io_pressure(load_plan):
    spill = parse_explain(load_plan("sort_spill"))
    assert spill.root.io_pressure == "temp"
    assert spill.root.children[0].io_pressure == "disk"
    raw = load_plan("seqscan_filter_waste")
    scan = raw[0]["Plan"]["Plans"][0]
    scan["Shared Read Blocks"] = 0
    assert parse_explain(raw).root.children[0].io_pressure == "memory"
    assert parse_explain(load_plan("explain_only")).root.io_pressure == "none"


def test_accepts_wrapper_forms(load_plan):
    raw = load_plan("seqscan_filter_waste")
    from_str = parse_explain(json.dumps(raw))
    from_list = parse_explain(raw)
    from_dict = parse_explain(raw[0])
    from_bare = parse_explain(raw[0]["Plan"])
    assert isinstance(from_str, ParsedPlan)
    assert from_str.fingerprint == from_list.fingerprint == from_dict.fingerprint == from_bare.fingerprint


def test_rejects_garbage():
    with pytest.raises(ValueError):
        parse_explain({"nothing": "here"})
    with pytest.raises(ValueError):
        parse_explain("not json")


def test_fingerprint_ignores_numbers_but_not_shape(load_plan):
    a = parse_explain(load_plan("seqscan_filter_waste"))
    raw = load_plan("seqscan_filter_waste")
    raw[0]["Plan"]["Total Cost"] = 1.0
    raw[0]["Plan"]["Plans"][0]["Plan Rows"] = 7
    assert parse_explain(raw).fingerprint == a.fingerprint
    raw[0]["Plan"]["Plans"][0].update({"Node Type": "Index Scan", "Index Name": "idx_orders_customer"})
    assert parse_explain(raw).fingerprint != a.fingerprint
