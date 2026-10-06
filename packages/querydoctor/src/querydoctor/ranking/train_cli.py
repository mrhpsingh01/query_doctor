"""Command line: ``python -m querydoctor.ranking.train_cli``.

Kept apart from ``ranking.train`` on purpose: when a module runs as ``__main__`` the classes pickled into the model file
would be recorded as ``__main__._Models`` and could never be loaded by the API.
"""

from __future__ import annotations

from querydoctor.ranking.train import save_bundle, train_all


def main(argv: list[str] | None = None) -> int:
    import argparse

    from querydoctor.ranking.dataset import read_rows

    parser = argparse.ArgumentParser(description="Train the recommendation ranker from the measured dataset")
    parser.add_argument("--data", default="datasets/ranker-v1.jsonl")
    parser.add_argument("--out", default="models/ranker.joblib")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    rows = read_rows(args.data)
    bundle = train_all(rows, seed=args.seed)
    save_bundle(bundle, args.out)
    meta = bundle.meta
    print(f"trained on {len(rows)} rows; deployed model {meta['deployed_model']}; use_for_ranking={meta['use_for_ranking']}")
    print(meta["reason"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
