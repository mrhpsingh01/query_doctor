# Recommendation ranker: evaluation

This is a plain account of what the learned ranker does and does not do. The numbers come from
`models/ranker.meta.json`, produced by `python -m querydoctor.ranking.train_cli` on `datasets/ranker-v1.jsonl`.

## The data

* 3,670 rows: one per (query instance, index candidate), each with **planner-only features** and **measured labels**
  (median before and after a real index in an isolated clone, with one shared baseline per query instance).
* 965 concrete queries from 39 query templates (40 query shapes) on five data variants (tiny, small, medium, and two
  skews: Zipf 0.5 and 1.1). 47% of the candidates are deliberate decoys (wrong column order, a lone leading column, an
  unrelated column).
* 66% of candidates improved their query by at least 20% with identical output; 7% made it slower.

## How it was evaluated

* Group-based splits only. A group is a query shape; a shape is in training or in testing, never in both. 11 of the 40
  groups were held out, and `GroupKFold(5)` was run over the rest.
* Models: the planner-score heuristic (baseline), logistic regression, LightGBM regressor (log speedup), LightGBM
  classifier (isotonic-calibrated), LightGBM LambdaRank.
* `use_for_ranking` is true only if the best learned model beats the heuristic on held-out NDCG@3 (by 0.02) and
  precision@3, **and** a paired bootstrap over groups of the NDCG@3 gain on every out-of-fold query excludes zero (90%
  interval). The extra guard exists because the bare rule let a pure-noise model through in 1 of 8 trial datasets; with
  it the false-positive rate on noise was 1 in 20 and a planted signal always passed.

## Result: the heuristic wins (or ties)

| Model | Held-out NDCG@3 | Precision@3 | Top pick improves | Cross-validated NDCG@3 |
|---|---|---|---|---|
| Planner score (heuristic) | 0.986 | 0.792 | 0.978 | 0.977 ± 0.014 |
| Logistic regression | 0.986 | 0.791 | 0.978 | 0.986 ± 0.009 |
| LightGBM regressor | 0.986 | 0.791 | 0.978 | 0.986 ± 0.011 |
| LightGBM classifier | 0.986 | 0.791 | 0.978 | 0.978 ± 0.016 |
| LightGBM LambdaRank | 0.986 | 0.791 | 0.978 | 0.988 ± 0.010 |

The best learned model's paired gain over the heuristic is +0.001 NDCG@3 (90% interval −0.002 to +0.004), so
`use_for_ranking` is **false**: suggestions are still ordered by the planner score, and the model's output is shown as a
separate, labelled prediction.

Why nothing separates: each query has about four candidates and two thirds of them help, so the top three nearly always
contain a good index and the planner score already puts a good one first (97.8% of top picks improve). The benchmark
saturates the ranking metrics; this dataset cannot show a ranking advantage even if one exists. Harder candidate sets
(more candidates, fewer useful ones) would be needed to tell the models apart.

## What the model does add

* A calibrated probability that an index helps (held-out Brier 0.040, expected calibration error 0.029 raw and 0.019
  after isotonic calibration).
* A speedup estimate with a held-out mean absolute error of 0.51 in log speedup (a typical miss of about 1.7x). It can
  overshoot badly on queries unlike the training data: the headline query is predicted at about 138x faster, against 25x
  measured earlier. Treat it as an order of magnitude, never as a promise.

## Known limits

* Rows come from clones of a synthetic shop schema. The write-ratio feature is constant in clones (statistics do not
  survive `CREATE DATABASE ... TEMPLATE`), so the model cannot learn it.
* Timings of sub-millisecond queries are noisy; 68 rows have a baseline under 0.2 ms.
* Only 40 query shapes: generalisation to real, differently shaped workloads is unproven. The `/models` page and
  `GET /api/model/monitor` compare predictions with later benchmarks and flag drift (at least 20 outcomes and a mean
  error above 1.5x the training error).
