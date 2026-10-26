# Figure 19b: single-writer throughput, OzoneKV vs SlateDB (sync and async)

Paper: `images/experiment/single_throughput.pdf` (§5). The figure shows OzoneKV-async,
OzoneKV-sync, SlateDB-async and SlateDB-sync on workloads A, B, C, D and F with one
writer. The deployment is distributed: Corfu/MinIO backs OzoneKV, and S3 backs SlateDB.

| File | What it is |
| --- | --- |
| `paper_figure.pdf` | the figure as it appears in the paper |
| `data/ozonedb/` | OzoneKV-async: 15 `.result` files (5 workloads × 3 trials) |
| `data/ozonedb-durable/` | OzoneKV-sync: 5 files. There is 1 trial per workload, so there are no error bars |
| `data/slatedb/`, `data/slatedb-durable/` | SlateDB-async and SlateDB-sync: 15 files each |
| `scripts/plot_throughput.py` | the plot script (`--results-dir`, default `../data`; `--output`). It reads the archived file names and the current runner's aggregate files. A system without files is skipped with a warning |
| `scripts/ozonekv/` | the OzoneKV multi-node script from `bench/scripts/`, run with one writer. The `--write-mode` flag below lives in `bench/scripts/` (the in-repo copy), not in this snapshot. Run `local/load_corfu_dataset.sh`, then `local/run_multinode_ycsb_with_corfu.sh`. That script calls `local/run_multinode_ycsb.py`, which calls `local/run_local_ycsb_multiproc.py` |
| `scripts/slatedb/scripts/sweep_single_writer.sh` | the SlateDB fork's `slatedb-ycsb` workload sweep. `AWAIT_DURABLE=1` gives SlateDB-sync. Comes with `SlateDb.toml` and `workloads/` |

All runs use 1 writer, 1 M × 1 KB records and 300 s. The directory names predate the
async/sync naming: `-durable` means sync. The error bars are the stdev across trials.

OzoneKV-sync is the engine default: a put is acked once it is sequenced in the shared
log (`corfu_sync_writes = true`). OzoneKV-async acks a put from the writer's buffer and
submits the buffer every `corfu_commit_interval_ms` (`--write-mode async`, default
10 ms), so an ack precedes durability by up to one interval.

## Reproduce the plot

```bash
cd scripts
python3 plot_throughput.py --output ../single_throughput.pdf
```

## Regenerate the data (cluster)

OzoneKV: load with `bench/scripts/local/load_corfu_dataset.sh`, then run the multi-node
runner with one writer, once per write mode:

```bash
bash bench/scripts/local/run_multinode_ycsb_with_corfu.sh --writers-list 1 \
    --workloads "a b c d f" --duration 300 --trials 3 --run-tag fig19b-sync        # OzoneKV-sync
bash bench/scripts/local/run_multinode_ycsb_with_corfu.sh --writers-list 1 \
    --workloads "a b c d f" --duration 300 --trials 3 --run-tag fig19b-async \
    --write-mode async                                                              # OzoneKV-async
```

Copy the `*_agg_multinode_w1_t1_trial*.result` files of the sync run into
`data/ozonedb-durable/` and those of the async run (token `ozonedb-corfu-async-native`)
into `data/ozonedb/`; the plot script reads both the archived and these names. SlateDB:
run `sweep_single_writer.sh` once plain and once with `AWAIT_DURABLE=1`. Each system and
mode takes 5 workloads × 300 s per trial.
