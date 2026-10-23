# Figure 19b: single-writer throughput, OzoneKV vs SlateDB (sync and async)

Paper: `images/experiment/single_throughput.pdf` (§5).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper |
| `plot_throughput.py` | plot script |

## Reproduce the plot

```bash
uv run plot_throughput.py --results-dir <results> --output paper_figure.pdf
# or python3 (needs matplotlib, numpy)
```

Expected layout under `<results>` (one `.result` file per trial; error bars are
the stdev across trials):

```
ozonedb/1KB-300s-1000000-workload{w}-ozonedb-corfu_w1_t1_trial{n}.result
ozonedb-durable/1KB-300s-1000000-workload{w}-ozonedb-corfu-durable_w1_t1_trial{n}.result
slatedb/1KB-300s-1000000-workload{w}-slatedb-w1-t1-trial{n}.result
slatedb-durable/1KB-300s-1000000-workload{w}-slatedb-durable-w1-t1-trial{n}.result
```

`-durable` folders hold the sync runs; the plain folders hold the async runs.

## Status: data missing

None of the four result folders are here or anywhere under `revision/`; they
are probably still on the AWS run machines.
