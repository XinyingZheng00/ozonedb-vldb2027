# Figure 1a: first-generation embedded DB (RocksDB reopen-per-op)

Paper: `images/first_generation.pdf`, `\label{fig:first_generation}` (§2 Motivation).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper |
| `plot_reopen_results.py` | plot script |

## Reproduce the plot

```bash
uv run plot_reopen_results.py results_20260219_180411.csv -o paper_figure.pdf
# or: python3 plot_reopen_results.py results_20260219_180411.csv -o paper_figure.pdf   (needs matplotlib)
```

With no CSV argument the script picks the newest `results_*.csv` in the current directory.

## Status: data missing

`results_20260219_180411.csv` is not in this folder. It was produced by
`reopen_benchmark.sh`, which runs RocksDB `db_bench` with `--threads=T`, once as a
baseline and once with `--reopen_after_each_op`. Neither the CSV nor the script
was found anywhere under `revision/`; they are probably still on the machine
that ran the benchmark.

Note: the CSV's `mode` values are misnomers. `multi-thread` is the baseline and
`multi-process` is the reopen-per-op run (both are one `db_bench` process).
