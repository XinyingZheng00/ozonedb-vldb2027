# Figure 2a: SQLite multi-process throughput (workload D)

Paper: `images/experiment/sqlite_workloadd.pdf`, `\label{fig:second_generation_local}` (§2 Motivation).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper |
| `data/results.csv` | the mean and stddev throughput per (system, workload, writers). It is identical to `figure9_multi_process_writer/data/fig2-multi-writer-local/results.csv` |
| `scripts/plot_sqlite_workload.py` | the plot script (SQLite trunk versus BCW2 versus HCTree) |
| `scripts/run_fig2_sweep.py` | the benchmark orchestrator that produced `results.csv` |

## Reproduce the plot

```bash
cd scripts
python3 plot_sqlite_workload.py d --csv ../data/results.csv --outdir ..
# writes ../sqlite_workloadd.pdf  (same as paper_figure.pdf)
```

The script needs Python 3.11+ and matplotlib. You can also run
`uv run plot_sqlite_workload.py ...`. Pass `all` instead of `d` to draw every workload.

## Regenerate the data (CloudLab node)

`run_fig2_sweep.py` runs the (system x workload x writers x repeat) matrix with YCSB-cpp.
It writes per-run logs and `results.csv`. It reads these paths from the environment:

- `YCSB_CPP_DIR` (default `~/YCSB-cpp`, commit `8401bde`), built with the SQLite variants
  (trunk, BCW2, HCTree). The build scripts for the baselines are not in this repo. The HCTree
  and BCW2 settings are in `YCSB-cpp/sqlite/*.properties`.
- `ROCKSDB_PREFIX` (default `~/rocksdb-9.6.1`)
- `OZONEDB_HOME` (default `~/ozonedb`)

See `figure9_multi_process_writer/data/fig2-multi-writer-local/REPORT.md` for the full run
procedure.
