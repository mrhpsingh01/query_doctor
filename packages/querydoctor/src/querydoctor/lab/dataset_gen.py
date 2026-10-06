"""Generate the recommendation-ranker dataset (hours of measurement; run it in the background and resume freely).

    python -m querydoctor.lab.dataset_gen --out datasets/ranker-v1.jsonl --per-template 4 \\
        --variants tiny small skew_low skew_high medium

Rows are appended as each query instance finishes, and instances already present in ``--out`` are skipped, so an
interrupted run continues where it stopped.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys
import time

from querydoctor.benchmark import BenchmarkConfig
from querydoctor.lab.query_templates import QueryInstance, sample_instances
from querydoctor.lab.variants import VARIANTS, ensure_variant
from querydoctor.ranking.dataset import VariantEnv, build_rows, read_rows, write_rows

DEFAULT_BENCH_DSN = "postgresql://qd_bench:qd_bench@127.0.0.1:5434/postgres"
DEFAULT_SUPER_DSN = "postgresql://querydoctor:querydoctor@127.0.0.1:5434/postgres"


def pending_instances(variant: str, per_template: int, seed: int, done: set[str]) -> list[QueryInstance]:
    """The instances of one variant whose rows are not in the output file yet."""
    return [i for i in sample_instances(variant, per_template, seed) if i.query_id not in done]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the ranker dataset by measuring index candidates")
    parser.add_argument("--out", default="datasets/ranker-v1.jsonl")
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=VARIANTS)
    parser.add_argument("--per-template", type=int, default=3, help="query instances per template and variant")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--runs", type=int, default=5, help="measured runs per side (5-10)")
    parser.add_argument("--timeout", type=int, default=600, help="seconds allowed per query instance")
    parser.add_argument("--bench-dsn", default=DEFAULT_BENCH_DSN)
    parser.add_argument("--super-dsn", default=DEFAULT_SUPER_DSN, help="superuser on the bench instance (installs hypopg)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    out = pathlib.Path(args.out)
    done = {r.query_id for r in read_rows(out)}
    config = BenchmarkConfig(runs=args.runs, warmups=1, timeout_s=args.timeout)
    started, total = time.monotonic(), 0
    for variant in args.variants:
        todo = pending_instances(variant, args.per_template, args.seed, done)
        if not todo:
            print(f"{variant}: nothing left to do")
            continue
        template = ensure_variant(args.super_dsn, variant, log=print)
        env = VariantEnv(bench_dsn=args.bench_dsn, variant=variant, template=template)
        print(f"{variant}: {len(todo)} query instances to measure")
        counter = {"n": 0}

        def progress(inst, rows):
            counter["n"] += 1
            write_rows(out, rows, append=True)
            done.add(inst.query_id)
            print(f"  [{variant} {counter['n']}/{len(todo)}] {inst.template_id}: {len(rows)} rows ({(time.monotonic() - started) / 60:.1f} min)", flush=True)

        for _row in build_rows(env, todo, config, on_instance=progress):
            total += 1
    print(f"done: {total} new rows in {(time.monotonic() - started) / 60:.1f} minutes -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
