# Figure 13: distributed compaction

Paper: `images/experiment/completion_time_vs_writers.pdf`, `\label{fig:dist_compaction}` (§5).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper. It is identical to `data/v4_20260430T213314/plots/completion_time_vs_writers.pdf`. |
| `data/v4_20260430T213314/trials.csv` | one row per trial (writers, completion time, generations, ...) |
| `data/v4_20260430T213314/per_trial/`, `run_config.json` | the per-trial writer logs and the run configuration |
| `scripts/make_plots.py` | the plot script |
| `scripts/churn_runner.py` (with `task_log_parser.py`, `record_pb2.py`, `churn_config.json`) | the experiment runner |
| `scripts/experiment_design.md` | the design, with the source patches |

## Reproduce the plot

```bash
python3 scripts/make_plots.py data/v4_20260430T213314/trials.csv
# This writes data/v4_20260430T213314/plots/completion_time_vs_writers.pdf (the paper figure).
```

This command overwrites the PDFs in `data/.../plots/`. If you want to keep
the originals, copy the run folder first.

## Regenerate the data (CloudLab node)

```bash
cd $OZONEDB_HOME   # the ozonedb checkout
# One time only: apply the six source patches and rebuild. See scripts/experiment_design.md, "Patches & rebuild".
python3 <this folder>/scripts/churn_runner.py --vary writers
```
