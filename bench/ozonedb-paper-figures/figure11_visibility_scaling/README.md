# Figure 11: visibility scaling

Paper: `images/experiment/visibility_scaling.pdf`, `\label{fig:visibility_scaling}` (§5.3).
The figure is a CDF of the time from a writer's ack to the first successful read. It has
one facet each for 2, 4, 8 and 16 writers. The curves are OzoneKV linearizable,
CR-SQLite-sync and CR-SQLite-async at three sync intervals.

| File | What it is |
| --- | --- |
| `paper_figure.pdf` | the figure as it appears in the paper |
| `data/crsqlite/visibility-xnode-20260821-170053` … `-170548` | 16 runs. At each of 2, 4, 8 and 16 writers: CR-SQLite-sync (`cr-sqlite-syncread`, read barrier) and CR-SQLite-async at sync intervals of 10, 50 and 200 ms |
| `data/ozonedb/visibility-xnode-20260821-{170608,170635,170703,174650}` | OzoneKV linearizable at 2, 4, 8 and 16 writers |
| `scripts/plot_consistency.py` | the plot script. Use the `visibility-scaling` subcommand. The script also has `staleness`, `lost-updates`, `visibility`, `visibility-sweep` and `table` |
| `scripts/ozonekv/local/run_visibility_cross_node.sh` | `--linearizable --writers N`. Starts `consistency.py visibility-reader` on one client and `visibility-writer` on another. Pulls the results and reduces them with `visibility_analysis.py` |
| `scripts/ozonekv/consistency.py`, `visibility_analysis.py`, `ycsb_config.py`, `config/ycsb.example.yaml` | the OzoneKV side. Copied from `bench/scripts/` on 2026-10-21 |
| `scripts/crsqlite/local/run_visibility_cross_node.sh` | `--read-barrier` (sync) or `--interval-ms M` (async), with `--num-writers N`. Drives `bench.py visibility-reader` and `visibility-writer` |
| `scripts/crsqlite/bench.py`, `bench_lib/`, `sync_daemon.py`, `generate_workload.py`, `ycsb_config.py` | the CR-SQLite side, from the crsqlite repo |

Each run directory holds two files:

- `summary.json`: the parameters and the percentiles.
- `visibility.csv`: one row per insert. The plot reads this file.

All 20 runs are cross-node. The writers run on one client. The reader runs alone on another
client. The writer sends the ack over TCP. The latency is measured from the time the reader
receives the notification. Each writer inserts 20 keys/s for 15 s with 1000 B values.

## Reproduce the plot

```bash
cd scripts
python3 plot_consistency.py visibility-scaling ../data/crsqlite/* ../data/ozonedb/* \
  --out visibility_scaling.pdf
# writes scripts/out/visibility_scaling.pdf
```

The script needs matplotlib and numpy. The script writes every figure into an `out/`
directory next to itself. For this reason, `--out` takes a file name, not a path. The
default layout is the paper's layout: a 2×2 grid with the legend at the bottom.

## Regenerate the data (cluster)

Use three nodes. The writers run on one node. The reader runs on a second node. Corfu
and MinIO run on the third node. For each writer count, run two scripts:

- `scripts/ozonekv/local/run_visibility_cross_node.sh --linearizable --writers N`
- from the crsqlite repo, `run_visibility_cross_node.sh --num-writers N`, once with
  `--read-barrier` and once for each of `--interval-ms 10|50|200`.

All runs take about 1 h.
