# Figure 19b: single-writer throughput, OzoneKV vs SlateDB (sync and async)

Paper: `images/experiment/single_throughput.pdf` (§5). The figure shows OzoneKV-async,
OzoneKV-sync, SlateDB-async and SlateDB-sync on workloads A, B, C, D and F with one
writer. The deployment is distributed: Corfu backs OzoneKV, and S3 backs SlateDB.

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper |
| `data/ozonedb/` | OzoneKV-async: 15 `.result` files (5 workloads × 3 trials) |
| `data/ozonedb-durable/` | OzoneKV-sync: 5 files. There is 1 trial per workload, so there are no error bars |
| `data/slatedb/`, `data/slatedb-durable/` | SlateDB-async and SlateDB-sync: 15 files each |
| `scripts/plot_throughput.py` | the plot script (`--results-dir`, default `../data`; `--output`) |
| `scripts/ozonekv/` | the OzoneKV multi-node runner. Copied from `bench/scripts/` on 2026-10-21 and run with one writer. Run `local/load_corfu_dataset.sh`, then `local/run_multinode_ycsb_with_corfu.sh`. That script calls `local/run_multinode_ycsb.py`, which calls `local/run_local_ycsb_multiproc.py` |
| `scripts/slatedb/scripts/sweep_single_writer.sh` | the SlateDB fork's `slatedb-ycsb` workload sweep. `AWAIT_DURABLE=1` gives SlateDB-sync. Comes with `SlateDb.toml` and `workloads/` |

All runs use 1 writer, 1 M × 1 KB records and 300 s. The directory names predate the
async/sync naming: `-durable` means sync. The error bars are the stdev across trials.

## Reproduce the plot

```bash
cd scripts
python3 plot_throughput.py --output ../single_throughput.pdf
```

The script needs matplotlib and numpy (or `uv run`). It prints every mean ± stdev. The
paper's copy was drawn at a wider aspect ratio, with the legend to the right of the axes.
The script puts the legend inside the top-left corner. The bars and the error bars are the
same.

## Regenerate the data (cluster)

OzoneKV: run the multi-node runner with one writer
(`run_multinode_ycsb_with_corfu.sh --writers-list 1 --workloads "a b c d f" --duration 300`).
Run it once with `--durable` and once without. SlateDB: run `sweep_single_writer.sh` once
plain and once with `AWAIT_DURABLE=1`. Each system and mode takes 5 workloads × 300 s.

Notes:

- An April version of the runner aggregated the OzoneKV files on 2026-04-27 and
  2026-04-28. The per-writer files named in each file's footer were not kept. Today's
  runner names the results differently (`…-native_agg_multinode_…`).
- The result files do not record whether a run was async or sync. Only the directory
  name does.
