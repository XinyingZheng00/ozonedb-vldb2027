# Figure 18: MLflow use case

Paper: `images/experiment/mlflow_combined.pdf`, `\label{fig:usecase-mlflow}` (§5 Use cases).
The figure has three panels: the "database is locked" error rate, the resource usage, and the training-step time.

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper |
| `data/ray_mlflow_swapin_3thread_5s_psweep/` | the error-rate run: `trials.csv` plus one folder per run (sqlite, ozonedb, postgres; 1-48 processes) |
| `data/ray_mlflow_swapin_resource_usage/trials.csv` | the resource-usage run |
| `data/ray_mlflow_swapin_compute_time/trials.csv` | the training-step-time run |
| `usecases/mlflow/plots/plot_combined.py` | the paper figure script. It draws all three panels in one figure |
| `usecases/mlflow/plots/plot_error_rate.py`, `plot_resource_usage.py`, `plot_training_step_time.py` | one script per panel. The shared code is in `plot_common.py` |
| `usecases/mlflow/run_ray_swapin.py`, `ray_trainable.py`, `ozonedb_metric_store.py`, ... | the benchmark driver, the Ray Tune trainable, and the MLflow store backed by OzoneDB. The store calls OzoneDB through `libozonedb_pyshim.so`. This library is built from `ozonedb/bench/usecases/mlflow/ozonedb_pyshim.cpp` with the rest of OzoneDB. |

## Reproduce the plot

```bash
cd usecases/mlflow/plots
python3 plot_combined.py --out ../../../paper_figure.pdf
# Without --out, the script writes mlflow_combined.pdf beside itself.
```

The script needs matplotlib. It reads all three `data/*/trials.csv` files. Its
output matches `paper_figure.pdf`. We rendered both at 2000 px wide: no pixel
differs, and every label is within 0.1 pt of its position in the paper.

The original script that combined the panels was lost. So we rebuilt
`plot_combined.py` on 2026-10-20 from the PDF of the paper. It keeps the
details of the paper:
- The script draws only OzoneDB, SQLite and Postgres. It leaves out the `postgres_serialized` control.
- Some lines are offset, so they do not overlap ("Lines perturbed slightly for clarity" in the caption). In the error-rate panel, OzoneDB is offset by +0.0015 and Postgres by +0.003. Their real values are 0. In the RSS panel, SQLite is offset by +0.8 MB. Its real value is 0.
- The RSS panel uses a symlog y-axis fixed to −2 … 2722 MB. The compute-time panel uses a linear y-axis.

The single-panel scripts (`plot_error_rate.py`, `plot_resource_usage.py`,
`plot_training_step_time.py`) draw the same data as separate figures with
titles and without offsets. They are not in the paper.

## Regenerate the data (Linux node)

The setup is Postgres, `pip install mlflow psycopg2-binary "ray[tune]" psutil`,
and the MLflow `busy_timeout` set to 5000. The error-rate run is:

```bash
cd usecases/mlflow
python3 run_ray_swapin.py --processes 1,2,4,8,16,20,24,28,32,48 \
  --k-steps 100 --n-samples 2000 --cpus-per-trial 0.3 --repeats 1 \
  --backends sqlite,ozonedb,postgres \
  --with-monitor --monitor-interval-s 0.5 --with-disk-monitor --disk-monitor-interval-s 0.5 \
  --out-dir <out>/ray_mlflow_swapin_3thread_5s_psweep
```
