"""Regenerates the hand-written PG16-format plan fixtures in fixtures/postgres_plans/.

These are compact, deterministic plans used by unit tests. Real plans captured from the lab are written by
scripts/capture_plans.py as live_*.json.
"""

import json
import pathlib

F = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "postgres_plans"
F.mkdir(parents=True, exist_ok=True)


def w(name, plan):
    (F / f"{name}.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")


# 1. Large filtered seq scan under a Sort (ANALYZE, BUFFERS)
w("seqscan_filter_waste", [{
    "Plan": {
        "Node Type": "Sort", "Parallel Aware": False, "Startup Cost": 61500.10, "Total Cost": 61527.20,
        "Plan Rows": 10900, "Plan Width": 48,
        "Actual Startup Time": 741.50, "Actual Total Time": 741.90, "Actual Rows": 10842, "Actual Loops": 1,
        "Output": ["id", "total_amount", "created_at"], "Sort Key": ["created_at DESC"],
        "Sort Method": "quicksort", "Sort Space Used": 1200, "Sort Space Type": "Memory",
        "Shared Hit Blocks": 120, "Shared Read Blocks": 18000, "Temp Read Blocks": 0, "Temp Written Blocks": 0,
        "Plans": [{
            "Node Type": "Seq Scan", "Parent Relationship": "Outer", "Parallel Aware": False,
            "Relation Name": "orders", "Schema": "public", "Alias": "orders",
            "Startup Cost": 0.0, "Total Cost": 60400.00, "Plan Rows": 10900, "Plan Width": 48,
            "Actual Startup Time": 0.040, "Actual Total Time": 735.20, "Actual Rows": 10842, "Actual Loops": 1,
            "Output": ["id", "total_amount", "created_at"],
            "Filter": "(orders.customer_id = 4242)", "Rows Removed by Filter": 1813561,
            "Shared Hit Blocks": 120, "Shared Read Blocks": 18000, "Temp Read Blocks": 0, "Temp Written Blocks": 0,
        }],
    },
    "Settings": {"work_mem": "4MB", "random_page_cost": "4"},
    "Planning Time": 0.210, "Execution Time": 742.10,
}])

# 2. Repeated nested loop: inner seq scan executed 2000 times
w("nested_loop_repeated", [{
    "Plan": {
        "Node Type": "Nested Loop", "Join Type": "Inner", "Parallel Aware": False,
        "Startup Cost": 0.0, "Total Cost": 90000.0, "Plan Rows": 10000, "Plan Width": 60,
        "Actual Startup Time": 0.10, "Actual Total Time": 6200.0, "Actual Rows": 9800, "Actual Loops": 1,
        "Join Filter": "(o.customer_id = c.id)",
        "Plans": [
            {"Node Type": "Seq Scan", "Parent Relationship": "Outer", "Parallel Aware": False,
             "Relation Name": "customers", "Alias": "c", "Startup Cost": 0.0, "Total Cost": 200.0,
             "Plan Rows": 2000, "Plan Width": 20, "Actual Startup Time": 0.02, "Actual Total Time": 4.0,
             "Actual Rows": 2000, "Actual Loops": 1, "Filter": "(c.segment = 'vip')",
             "Rows Removed by Filter": 8000},
            {"Node Type": "Seq Scan", "Parent Relationship": "Inner", "Parallel Aware": False,
             "Relation Name": "orders", "Alias": "o", "Startup Cost": 0.0, "Total Cost": 45.0,
             "Plan Rows": 5, "Plan Width": 40, "Actual Startup Time": 1.0, "Actual Total Time": 3.0,
             "Actual Rows": 5, "Actual Loops": 2000, "Filter": "(o.customer_id = c.id)",
             "Rows Removed by Filter": 99995},
        ],
    },
    "Planning Time": 0.3, "Execution Time": 6201.0,
}])

# 3. Sort that spills to disk
w("sort_spill", [{
    "Plan": {
        "Node Type": "Sort", "Parallel Aware": False, "Startup Cost": 150000.0, "Total Cost": 152500.0,
        "Plan Rows": 1000000, "Plan Width": 40, "Actual Startup Time": 2100.0, "Actual Total Time": 2300.0,
        "Actual Rows": 1000000, "Actual Loops": 1, "Sort Key": ["total_amount"],
        "Sort Method": "external merge", "Sort Space Used": 41000, "Sort Space Type": "Disk",
        "Shared Hit Blocks": 10, "Shared Read Blocks": 9000, "Temp Read Blocks": 5100, "Temp Written Blocks": 5120,
        "Plans": [{
            "Node Type": "Seq Scan", "Parent Relationship": "Outer", "Parallel Aware": False,
            "Relation Name": "orders", "Alias": "orders", "Startup Cost": 0.0, "Total Cost": 20000.0,
            "Plan Rows": 1000000, "Plan Width": 40, "Actual Startup Time": 0.02, "Actual Total Time": 500.0,
            "Actual Rows": 1000000, "Actual Loops": 1, "Shared Hit Blocks": 10, "Shared Read Blocks": 9000,
        }],
    },
    "Planning Time": 0.1, "Execution Time": 2400.0,
}])

# 4. Planner-only (no ANALYZE)
w("explain_only", [{
    "Plan": {
        "Node Type": "Seq Scan", "Parallel Aware": False, "Relation Name": "orders", "Schema": "public",
        "Alias": "orders", "Startup Cost": 0.0, "Total Cost": 60400.0, "Plan Rows": 1000000, "Plan Width": 80,
        "Output": ["id", "customer_id", "status"], "Filter": "(orders.status = 'pending'::text)",
    },
    "Settings": {"work_mem": "4MB"},
}])

if __name__ == "__main__":
    print("fixtures written to", F)
