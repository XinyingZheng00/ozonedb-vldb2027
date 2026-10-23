# Figure 1b: time breakdown of db_bench UpdateRandom

Paper: `images/time_breakdown.pdf` (§2 Motivation).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper |
| `plot_time_breakdown.py` | plot script (stacked bars: Open/Close vs Operations) |
| `rocksdb_changes.patch` | change to `analyse_time_share.py` in the RocksDB experiment repo, the script that computed the time shares (counts inclusive time, not only the leaf frame) |

## Reproduce the plot

```bash
uv run plot_time_breakdown.py time_breakdown.csv -o paper_figure.pdf
# or: python3 plot_time_breakdown.py time_breakdown.csv -o paper_figure.pdf   (needs matplotlib, numpy)
```

Each bar is renormalized to 100% over the two categories, so it shows their
ratio, not their share of wall-clock time.

## Status: data missing

`time_breakdown.csv` (the profiled time shares) is not in this folder and was
not found anywhere under `revision/`. To regenerate it: profile `db_bench
UpdateRandom` (baseline and reopen-per-op), run `analyse_time_share.py` with
`rocksdb_changes.patch` applied, and write the two percentages per bar to the
CSV.
