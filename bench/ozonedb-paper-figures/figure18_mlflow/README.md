# Figure 18: MLflow use case

Paper: `images/experiment/mlflow_combined.pdf`, `\label{fig:usecase-mlflow}` (§5 Use cases).
Three panels: "database is locked" error rate, resource usage, training-step time.

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper |
| `data/ray_mlflow_swapin_3thread_5s_psweep/` | error-rate run: `trials.csv` plus per-run folders (sqlite, ozonedb, postgres; 1-48 processes) |
| `data/ray_mlflow_swapin_resource_usage/trials.csv` | resource-usage run |
| `data/ray_mlflow_swapin_compute_time/trials.csv` | training-step-time run |
| `usecases/mlflow/plots/plot_combined.py` | paper figure script: all three panels in one figure |
| `usecases/mlflow/plots/plot_error_rate.py`, `plot_resource_usage.py`, `plot_training_step_time.py` | one script per panel (shared code in `plot_common.py`) |
| `usecases/mlflow/run_ray_swapin.py`, `ray_trainable.py`, `ozonedb_metric_store.py`, ... | benchmark driver, Ray Tune trainable, MLflow store backed by OzoneDB. The store calls OzoneDB through `libozonedb_pyshim.so`, built from `ozonedb/bench/usecases/mlflow/ozonedb_pyshim.cpp` with the rest of OzoneDB. |
| `usecases/PHASE_REPORT.md` | full write-up and exact run commands |

## Reproduce the plot (verified)

```bash
cd usecases/mlflow/plots
python3 plot_combined.py --out ../../../paper_figure.pdf
# without --out it writes mlflow_combined.pdf beside the script
```

Needs matplotlib. The script reads all three `data/*/trials.csv` files. Its
output matches `paper_figure.pdf`: rendered at 2000 px wide, no pixel differs,
and every label is within 0.1 pt of the paper's position.

The original combining script was lost, so `plot_combined.py` was rebuilt on
2026-10-20 from the paper's PDF. It keeps the paper's details:
- Only OzoneDB, SQLite and Postgres are drawn; the `postgres_serialized` control is left out.
- Some lines are offset so they don't overlap ("Lines perturbed slightly for clarity" in the caption). In the error-rate panel OzoneDB is +0.0015 and Postgres +0.003 (their values are really 0). In the RSS panel SQLite is +0.8 MB (really 0).
- The RSS panel uses a symlog y-axis fixed to −2 … 2722 MB. The compute-time panel uses a linear y-axis.

The single-panel scripts (`plot_error_rate.py`, `plot_resource_usage.py`,
`plot_training_step_time.py`) draw the same data as separate, unperturbed
figures with titles; they aren't in the paper.

## Regenerate the data (Linux node)

One-time setup (Postgres, `pip install mlflow psycopg2-binary "ray[tune]" psutil`,
MLflow `busy_timeout` set to 5000) and the exact `run_ray_swapin.py` commands
are in `usecases/PHASE_REPORT.md`. The error-rate run is:

```bash
cd usecases/mlflow
python3 run_ray_swapin.py --processes 1,2,4,8,16,20,24,28,32,48 \
  --k-steps 100 --n-samples 2000 --cpus-per-trial 0.3 --repeats 1 \
  --backends sqlite,ozonedb,postgres \
  --with-monitor --monitor-interval-s 0.5 --with-disk-monitor --disk-monitor-interval-s 0.5 \
  --out-dir <out>/ray_mlflow_swapin_3thread_5s_psweep
```
