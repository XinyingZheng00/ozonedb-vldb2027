# Figure 2a: SQLite multi-process throughput (workload D)

Paper: `images/experiment/sqlite_workloadd.pdf`, `\label{fig:second_generation_local}` (§2 Motivation).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper |
| `data/results.csv` | per (system, workload, writers) mean and stddev throughput; identical to `figure9_multi_process_writer/data/fig2-multi-writer-local/results.csv` |
| `scripts/plot_sqlite_workload.py` | plot script (SQLite trunk vs BCW2 vs HCTree) |
| `scripts/run_fig2_sweep.py` | benchmark orchestrator that produced `results.csv` |

## Reproduce the plot (verified)

```bash
cd scripts
python3 plot_sqlite_workload.py d --csv ../data/results.csv --outdir ..
# writes ../sqlite_workloadd.pdf  (same as paper_figure.pdf)
```

Needs Python 3.11+ and matplotlib (or `uv run plot_sqlite_workload.py ...`).
`all` instead of `d` draws every workload.

## Regenerate the data (CloudLab node)

`run_fig2_sweep.py` runs the (system x workload x writers x repeat) matrix with
YCSB-cpp and writes per-run logs plus `results.csv`. It uses absolute paths:
`/users/Xinying/YCSB-cpp` (commit `8401bde`, built by `ozonedb/bench/scripts/local/build_baselines.sh`,
which builds the SQLite library with `build_bcw2.sh` in the same folder; HCTree and BCW2
settings are in `YCSB-cpp/sqlite/*.properties`),
`/users/Xinying/rocksdb-9.6.1` and `/users/Xinying/ozonedb`. See
`figure9_multi_process_writer/data/fig2-multi-writer-local/REPORT.md` for the
full run procedure.
