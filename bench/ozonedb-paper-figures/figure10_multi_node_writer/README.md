# Figure 10: multi-node writers

Paper: `images/experiment/multi_throughput.pdf`, `\label{fig:3_coordination}` (§5.3).
The figure shows five systems, workloads A, B, C, D and F, and 2 to 32 writers.

| File | What it is |
| --- | --- |
| `paper_figure.pdf` | the figure as it appears in the paper |
| `data/strict100/steady_all.tsv` | OzoneKV (`ozonedb-corfu-linearizable-native-lru16g-dc50g-ch64k-adm`) and Cassandra-serial (`cassandra-serial`): campaign strict100-20260830, 100 GB, 300 s cells, 3 trials, one row per (cell, trial) |
| `data/crsqlite-300g/steady_all.tsv` | CR-SQLite-sync (`crsqlite-syncread`) and CR-SQLite-async-10ms (`crsqlite-sync10ms`), same method. 3 trials in 45 cells, 2 in the other 5. The 5 cells are all at 16 writers |
| `data/slatedb-durable/*.result` | SlateDB durable: 75 YCSB result files, 1 GB, 180 s cells, 3 trials. The plot takes the `[OVERALL]` whole-run throughput |
| `scripts/plot_throughput_writers.py` | the plot script. It reads `../data`, writes `../multi_throughput.pdf` and prints every cell |
| `scripts/multi_throughput_numbers.md` | the plotted means, tabulated |
| `scripts/extract_steady_throughput.py` | builds the two TSVs from per-writer YCSB result files (`--window 120 --tsv ...`) |
| `chain_trial.sh <trial>` | Runs Cassandra serial, then OzoneKV linearizable (16 GiB LRU cache, 50 GiB disk-cache tier), over all 25 cells. `README.md` describes it |
| `scripts/ozonekv/` | the OzoneKV multi-node chain as copied from `bench/scripts/` on 2026-10-21. `local/load_corfu_dataset.sh` loads. `local/run_multinode_ycsb_with_corfu.sh --linearizable` runs the sweep through `local/run_multinode_ycsb.py` and `local/run_local_ycsb_multiproc.py` on every client. `consistency.py` supplies the linearizable settings. `ycsb_config.py` and `config/ycsb.example.yaml` describe the cluster |
| `scripts/cassandra/` | `local/load_multinode_cassandra.sh`, `local/run_multinode_ycsb_with_cassandra.sh --consistency serial`, `cassandra_ctl.sh` (same runner chain with `--db_name cassandra`) |
| `scripts/crsqlite/` | from the crsqlite repo: `bench.py multinode`, `bench_lib/`, `sync_daemon.py`, `generate_workload.py`, `ycsb_config.py` |
| `scripts/slatedb/` | from the SlateDB fork's `slatedb-ycsb`: `scripts/sweep_num_writers.sh` (run with `AWAIT_DURABLE=1 DURATION=180`, writers 2 to 32, `--reopen-on-fence`), `SlateDb.toml`, `workloads/` |

The steady-state rate for the four 100 GB series is the mean `current ops/sec` over each
writer's last 120 s, summed over writers. The window matters. An OzoneKV linearizable
writer spends its first 45 to 90 s on the replay of the shared log. SlateDB was never
rerun at 100 GB, because its durable multi-writer path collapses at 32 writers. The
caption says so.

## Reproduce the plot

```bash
cd scripts
python3 plot_throughput_writers.py        # writes ../multi_throughput.pdf
```

The script needs matplotlib and numpy, or `uv run`. The printed cells match
`multi_throughput_numbers.md`. A different matplotlib version can shift the log-axis
tick decades. The bars are identical.

## Regenerate the data (cluster)

The cluster is one log/store node plus up to nine clients. Set `OZONEDB_HOME`, `${NODE_B}` and `${SSH_USER}`
first. One trial takes about 31 h per system before loads. For CR-SQLite, run
`bench.py multinode` from the crsqlite repo. For SlateDB, run `sweep_num_writers.sh`. Then
run `python3 extract_steady_throughput.py <results_dir> --window 120 --tsv steady_all.tsv`.
