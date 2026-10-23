# Figure 2b: SlateDB multi-writer throughput (await-durable)

Paper: `images/experiment/single-writer-await-durable-5ms-100ms-m6id-2xlarge-bar.pdf`,
`\label{fig:second_generation_dis}` (§2 Motivation).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper |
| `plot-multi-writer-bar.py` | plot script (grouped bars by writer count and flush interval) |

## Reproduce the plot

```bash
uv run --script plot-multi-writer-bar.py <results>.csv --out paper_figure.pdf
```

`uv` installs the pinned matplotlib/pandas/numpy versions from the script
header. Without `--out` it writes `<results>-bar.pdf` and `-bar.png` next to the CSV.

Input CSV: one row per run with at least `num_writers`, `flush_interval_ms`,
`effective_puts_per_s`. Repeats of the same pair are averaged; error bars are
the standard error.

## Status: data missing

The SlateDB results CSV (m6id.2xlarge, flush interval 5 ms and 100 ms) is not
in this folder and was not found anywhere under `revision/`; it is probably
still on the AWS run machines.
