# Figure 13: distributed compaction

Paper: `images/experiment/completion_time_vs_writers.pdf`, `\label{fig:dist_compaction}` (§5).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper (identical to `data/v4_20260430T213314/plots/completion_time_vs_writers.pdf`) |
| `data/v4_20260430T213314/trials.csv` | one row per trial (writers, completion time, generations, ...) |
| `data/v4_20260430T213314/per_trial/`, `run_config.json` | per-trial writer logs and the run configuration |
| `scripts/make_plots.py` | plot script |
| `scripts/churn_runner.py` (+ `task_log_parser.py`, `record_pb2.py`, `churn_config.json`) | experiment runner |
| `scripts/experiment_design.md`, `FINAL_REPORT.md` | design (including source patches) and results write-up |

## Reproduce the plot (verified)

```bash
python3 scripts/make_plots.py data/v4_20260430T213314/trials.csv
# writes data/v4_20260430T213314/plots/completion_time_vs_writers.pdf (the paper figure)
```

Note: this overwrites the PDFs in `data/.../plots/`. Copy the run folder first
if you want to keep the originals.

## Regenerate the data (CloudLab node)

```bash
cd $OZONEDB_HOME   # /users/Xinying/ozonedb
# one-time: apply the six source patches and rebuild (scripts/experiment_design.md, "Patches & rebuild")
python3 <this folder>/scripts/churn_runner.py --vary writers
```
