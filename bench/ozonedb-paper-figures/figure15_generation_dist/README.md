# Figure 15: generation distribution under churn

Paper: `images/experiment/generation_dist.pdf`, `\label{fig:generation_dist}` (§5, Churn Recovery).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper. It is identical to `data/v4_20260501T003206/plots/generation_dist.pdf`. |
| `data/v4_20260501T003206/trials.csv` | one row per trial. The `generation_distribution` column holds the per-task generation histogram. |
| `scripts/make_plots.py` | the plot script |
| `scripts/churn_runner.py` and the rest | the same experiment runner as figure 13 |

This figure uses the same run as table 4 (`v4_20260501T003206`). The run has
4 writers, abort ratio 1-50%, and 5 trials for each ratio.

## Reproduce the plot

```bash
python3 scripts/make_plots.py data/v4_20260501T003206/trials.csv
# This writes data/v4_20260501T003206/plots/generation_dist.pdf (the paper figure).
```

This command overwrites the PDFs in `data/.../plots/`. The full per-trial logs
of this run are in `table4_reassignment/data/v4_20260501T003206/per_trial/`.

## Regenerate the data

```bash
python3 scripts/churn_runner.py --vary abort
```

See `figure13_dist_compaction/README.md` for the setup. It covers the source
patches and `OZONEDB_HOME`.
