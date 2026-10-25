# Figure 1a: first-generation embedded DB (RocksDB reopen-per-op)

Paper: `images/first_generation.pdf`, `\label{fig:first_generation}` (§2 Motivation).
Caption: "Write throughput as the number of writers increases."

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper |
| `data/results_20260219_180411.csv` | db_bench `updaterandom`, 5 repeats per (mode, workers), 1 to 24 workers, 30 s each |
| `scripts/plot_reopen_results.py` | the plot script. Each point is the mean after the script drops the min and the max. The error bars show the stdev |
| `scripts/reopen_benchmark.sh` | the sweep that wrote the CSV. Run it from the root of the RocksDB fork as `tools/reopen_benchmark.sh` |
| `scripts/db_bench_reopen_after_each_op.patch` | the fork's `--reopen_after_each_op` / `--report_open_timing` change to `tools/db_bench_tool.cc` |

## Reproduce the plot

```bash
cd scripts
python3 plot_reopen_results.py ../data/results_20260219_180411.csv -o ../first_generation.pdf
```

The script needs matplotlib. You can also run `uv run plot_reopen_results.py ...`. The
PEP 723 header lists the dependency. If you give no CSV argument, the script picks the
newest `results_*.csv` in the current directory. The paper's copy is the same plot at a
wider aspect ratio.

Notes on the data:

- Both curves come from **one** `db_bench` process with `--threads=N`. The "processes"
  curve differs only by `--reopen_after_each_op`. This flag closes and reopens the DB
  around every operation. The paper calls this the multi-process setting. The CSV's
  `mode` values are misnomers. `multi-thread` is the baseline. `multi-process` is the
  reopen run.
- At 24 workers, all five reopen repeats recorded 0 ops/s. The script drops these points
  because the y axis is log-scaled.

## Regenerate the data (one node)

Use the RocksDB fork `github.com/oliverrogalski1001/rocksdb`, branch `main` (`fbfb6eced`).
This branch carries the db_bench patch. Build `db_bench` with
`make db_bench DEBUG_LEVEL=0 -j$(nproc)`. Copy `reopen_benchmark.sh` to `tools/`. Run it
from the repo root. The script writes `results_<timestamp>.csv` to the working directory.
It keeps its databases under `/tmp/rocksdbtest`.
