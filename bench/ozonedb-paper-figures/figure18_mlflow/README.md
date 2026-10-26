# Figure 18: MLflow use case

Paper: `images/experiment/mlflow_combined.pdf`, `\label{fig:usecase-mlflow}` (§5 Use cases).
Three panels: "database is locked" error rate, resource usage, training-step time.

```
run_fig18.sh                                   install Postgres/MLflow/Ray, run the P-sweep, draw the plot
scripts/run_ray_swapin.py                      benchmark driver
scripts/ray_trainable.py, ozonedb_metric_store.py, ozonedb_py.py, serialized_postgres_store.py, resource_sampler.py
                                               Ray Tune trainable, MLflow store backed by OzoneDB, its ctypes binding,
                                               the serialized-Postgres control, the resource sampler
scripts/orchestrator.py, config_template.py    shared helpers the driver imports
scripts/plot_combined.py                       paper figure script: all three panels
scripts/plot_common.py, plot_error_rate.py, plot_resource_usage.py, plot_training_step_time.py, plot_avg_latency.py, plot_architecture.py
                                               shared plot code and single-panel plots (not in the paper)
scripts/aggregate_repeats.py                   mean/stddev per configuration from a trials.csv (optional helper)
data/paper_data/ray_mlflow_swapin_3thread_5s_psweep/   the paper's error-rate run: trials.csv + per-run folders
data/paper_data/ray_mlflow_swapin_resource_usage/      the paper's resource-usage run
data/paper_data/ray_mlflow_swapin_compute_time/        the paper's training-step-time run
data/reproduced_data/20261024/                 rerun on amd133 (c6525-25g): one trials.csv with all three panels' columns
plots/paper_figure.pdf                         the figure in the paper
plots/reproduced_figure_20261024.pdf
plots/architecture.pdf                         architecture diagram (not in the paper)
```

The MLflow store calls OzoneDB through `libozonedb_pyshim.so`, built from
`ozonedb/bench/usecases/mlflow/ozonedb_pyshim.cpp` with the rest of OzoneDB.

## Rerun the experiment (Linux node with OzoneDB built)

```bash
bash run_fig18.sh     # -> data/reproduced_data/<date>/, plots/reproduced_figure_<date>.pdf
```

It installs PostgreSQL and the paper's versions (`mlflow==3.15.1`, `ray[tune]==2.57.0`),
sets MLflow's SQLite `busy_timeout` to 5000 ms as in the paper's runs (a permanent
change to that MLflow install), then runs P = 1-48 processes, 3 threads each, 100
steps on 2,000 samples, backends sqlite, ozonedb, postgres. One run now gives all
three panels; the paper used three runs because columns were added over time.
Postgres keeps its default 100-connection limit, so expect "too many clients"
errors at P >= 32; `ALTER SYSTEM SET max_connections = 300` and a restart avoid them.

## Redraw a plot

```bash
cd scripts
python3 plot_combined.py --out <file>       # the paper's three data folders by default (= paper_figure.pdf)
python3 plot_combined.py --csv ../data/reproduced_data/20261024/trials.csv --out <file>
```

Without `--out`, plot scripts write into `plots/`. Needs matplotlib.

The original combining script was lost, so `plot_combined.py` was rebuilt on
2026-10-20 from the paper's PDF; its output matches `paper_figure.pdf` (no pixel
differs at 2000 px wide). It keeps the paper's details:
- Only OzoneDB, SQLite and Postgres are drawn; the `postgres_serialized` control is left out.
- Some lines are offset so they don't overlap ("Lines perturbed slightly for clarity" in the caption). In the error-rate panel OzoneDB is +0.0015 and Postgres +0.003 (their values are really 0). In the RSS panel SQLite is +0.8 MB (really 0).
- The RSS panel uses a symlog y-axis fixed to −2 … 2722 MB. The compute-time panel uses a linear y-axis.
