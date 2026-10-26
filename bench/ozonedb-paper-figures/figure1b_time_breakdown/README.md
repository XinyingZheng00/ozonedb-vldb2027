# Figure 1b: time breakdown of db_bench UpdateRandom

Paper: `images/time_breakdown.pdf` (§2 Motivation). The figure shows the share of time in
open/close versus the share in operations. The workload is RocksDB `updaterandom` with 4
threads, reopen-per-op versus baseline.

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper |
| `data/time_breakdown.csv` | the share of profiled CPU samples in open/close and in operations, for the reopen run and the baseline run (4 rows). The header comments give the raw percentages |
| `scripts/plot_time_breakdown.py` | the plot script. It draws stacked bars. It renormalizes each bar to 100 % over the two categories |
| `scripts/rocksdb_workflow.sh` | builds db_bench and runs the sweep. The two `perf record` commands behind the CSV are commented out at the bottom |
| `scripts/analyse_time_share.py` | classifies the folded call chains of `perf report` into open/close and operations. It prints the shares |
| `scripts/analyse_time_share_inclusive.patch` | the change to `analyse_time_share.py` that gave the paper's numbers. It matches the whole call chain instead of only the leaf frame. It also counts the `close` syscalls |
| `scripts/reopen_benchmark.sh` | the sweep from the fork's `xinying` branch (`RUNNER_MODE=threads|processes`) |
| `scripts/db_bench_reopen_after_each_op.patch` | the fork's db_bench change on that branch |

## Reproduce the plot

```bash
cd scripts
python3 plot_time_breakdown.py ../data/time_breakdown.csv -t "" -o ../time_breakdown.pdf
```

The script needs matplotlib and numpy. You can also use `uv run`. The paper's copy has no
title, so the command passes `-t ""`. Without this flag, the script adds the title
"UpdateRandom 4 Threads: Reopen vs Baseline". The paper's copy is the same plot at a wider
aspect ratio.

The script renormalizes each bar to 100 % over the two categories. A bar shows the ratio
of the two categories, not their share of wall-clock time.

## Regenerate the data (one node)

Use the RocksDB fork `github.com/oliverrogalski1001/rocksdb`, branch `xinying` (`71680da88`).
Build db_bench with `DEBUG_LEVEL=1`. Load a database with `reopen_benchmark.sh`. Then run
the two `perf record` commands at the bottom of `rocksdb_workflow.sh`. These are the reopen
run and the baseline run, with `--threads=4`, 100 s. Apply
`analyse_time_share_inclusive.patch` to `analyse_time_share.py`. Run the script next to the
two `perf_updaterandom_*.data` files. Write the two shares per bar into `time_breakdown.csv`.
