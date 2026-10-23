# Figure 15: generation distribution under churn

Paper: `images/experiment/generation_dist.pdf`, `\label{fig:generation_dist}` (§5, Churn Recovery).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper (identical to `data/v4_20260501T003206/plots/generation_dist.pdf`) |
| `data/v4_20260501T003206/trials.csv` | one row per trial; `generation_distribution` holds the per-task generation histogram |
| `scripts/make_plots.py` | plot script |
| `scripts/churn_runner.py` and the rest | same experiment runner as figure 13 |

This uses the same run as table 4 (`v4_20260501T003206`, 4 writers, abort ratio 1-50%, 5 trials each).

## Reproduce the plot (verified)

```bash
python3 scripts/make_plots.py data/v4_20260501T003206/trials.csv
# writes data/v4_20260501T003206/plots/generation_dist.pdf (the paper figure)
```

Note: this overwrites the PDFs in `data/.../plots/`. The full per-trial logs of
this run are in `table4_reassignment/data/v4_20260501T003206/per_trial/`.

## Regenerate the data

```bash
python3 scripts/churn_runner.py --vary abort
```

See `figure13_dist_compaction/README.md` for setup (source patches, `OZONEDB_HOME`).
