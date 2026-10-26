# bench/scripts

`OZONEDB_HOME` must point at the repo root, and `config/ycsb.yaml` (copied from
`config/ycsb.example.yaml`) must describe the cluster. See the top-level `README.md` for setup, build and the runner flags.

## Node setup and build

| Script | Purpose |
| --- | --- |
| `setup.sh --role client\|corfu-server\|minio\|cassandra\|all` | provision a node; idempotent |
| `build.sh` | build the whole chain: `libOzoneDB.so`, the JNI shim, tests, the YCSB binding |
| `setup_disk_cache.sh` | format and mount one SSD as the disk-cache tier at `/tank/cache` |
| `setup_zfs.sh` | create the ZFS pool behind `/tank` |
| `corfu_local.sh`, `minio_local.sh` | single-machine CorfuDB and MinIO for the unit tests (no sudo) |
| `requirements.txt` | Python dependencies (PyYAML; matplotlib and numpy for `plot/`) |

## Shared helpers

| Script | Purpose |
| --- | --- |
| `ycsb_config.py` | the one loader for `config/ycsb.yaml`; derives every address from `nodes:` |
| `generate_workload.py` | writes the YCSB workload files the runners pass to YCSB |
| `config/` | `ycsb.example.yaml`, plus the RocksDB and SQLite tunings of the single-node comparison |
| `cassandra_ctl.sh`, `cassandra_cql.py` | control the Cassandra baseline on its server node |
| `server_sampler.sh` | per-cell server samples (MinIO counters, `du`, `pidstat`) for the cost model |

## Runners (`local/`)

The name `local/` is historical: it holds the single-machine runners and the multi-node
wrappers built on them.

| Script | Purpose | Figure |
| --- | --- | --- |
| `load_local_ycsb_multiproc.py`, `run_local_ycsb_multiproc.py` | N writer processes on one machine; every other runner builds on these | 9, 10, 12, 19b |
| `run_multinode_ycsb.py` | fan the multiproc runner out over the client hosts, pull results back | 10, 12, 19b |
| `run_ycsb_with_corfu.sh` | sweep workloads x writers on this machine, restarting the remote Corfu between cells | |
| `load_corfu_dataset.sh`, `run_multinode_ycsb_with_corfu.sh` | the cluster path for OzoneKV on Corfu (`--linearizable`, `--trials`, `--duration`) | 10, 12, 19b |
| `load_multinode_cassandra.sh`, `run_multinode_ycsb_with_cassandra.sh` | the same sweep against the Cassandra baseline (`--consistency one\|quorum\|serial`) | 10, 12 |
| `run_corfu_compaction_contention.py` | compaction commit latency vs writer count, driven by `run_ycsb_with_corfu.sh --script` | (not in the paper) |
| `run_consistency_with_corfu.sh`, `run_visibility_cross_node.sh` | laptop-side wrappers around `consistency.py` (fresh Corfu stream per run) | 11 |

## Consistency and extraction

| Script | Purpose | Figure |
| --- | --- | --- |
| `consistency.py` | staleness, lost-updates and visibility probes through the real JNI path; also owns `linearizable_corfu_settings` | 11 |
| `visibility_analysis.py` | reduces a visibility run's `visibility.csv` to `summary.json` | 11 |
| `extract_steady_throughput.py` | steady-state ops/s over the last `--window` seconds of each writer, as a TSV | 10 |
| `extract_cost_coefficients.py` | per-cell cost coefficients (requests, bytes, CPU) from writer results and server samples | 12 |

## Single-node comparison (`local/singlenode/`)

OzoneKV against RocksDB and the SQLite variants on one machine, through YCSB-cpp
(`YCSB_CPP_DIR`, `ROCKSDB_PREFIX`).

| Script | Purpose | Figure |
| --- | --- | --- |
| `run_fig2_load.py`, `run_fig2_load_tmpfs.py` | load phase on disk and on tmpfs; leaves the `cached_*` datasets | 19a |
| `run_fig2_sweep.py` | the (system x workload x threads x repeat) sweep; `--threads 1` is the single-writer run | 2a, 19a |
| `run_fig2_t1_chain.sh` | waits for a running sweep, then runs the `--threads 1` baseline | 19a |
| `rebuild_results_csv.py` | rebuild `results.csv` from the per-cell logs when a sweep ran in pieces | 2a, 9, 19a |
| `run_phase1_multiproc_sweep.py` | N processes, each on its own DB; also the helper module the next two import | 9 |
| `run_phase1_shared_db_sweep.py` | N processes on one shared DB; the Figure 9 data | 9 |
| `plot_fig2.py`, `plot_phase1_paper.py` | plots of the thread and process sweeps | 9 |

## Churn experiment (`local/churn_experiment/`)

Distributed compaction under writer churn. The runner loads with `compaction_enabled = false`,
snapshots, and runs every trial from the snapshot with `compaction_abort_rate` set (both are
config keys of the current code; the figure copies still describe the source patches of the
original run).

| Script | Purpose | Figure |
| --- | --- | --- |
| `churn_runner.py --vary writers\|abort\|both` | the experiment; imports `run_phase1_multiproc_sweep.py` | 13, 14ab, 15, Table 4 |
| `task_log_parser.py` | decodes `task.log`; generates `record_pb2.py` with `protoc` on first use | |
| `make_plots.py`, `plot_writer_activity.py` | completion time and generation plots; the writer-activity Gantt | 13, 14ab, 15 |
| `churn_config.json`, `experiment_design.md` | parameters and design notes | |

## Plots (`plot/`)

Output goes to `plot/out/` (gitignored) unless a script takes `--out`.

| Script | Input | Figure |
| --- | --- | --- |
| `plot_throughput.py` | multi-node aggregate and per-writer `.result` files; `faceoff` and `scaling` modes | 19b |
| `plot_single_writer.py --mode fig1\|fig1csv\|hbr` | `.result` files or the aggregated CSVs of the single-node sweep | 19a |
| `plot_consistency.py staleness\|lost-updates\|visibility\|visibility-sweep\|visibility-scaling\|table` | `consistency.py` output dirs | 11 |
| `plot_cost_model.py`, `paper_cost_figure.py`, `overlay_projections.py` | `coefficients.tsv` from `extract_cost_coefficients.py`, `prices.json`, `space.json` | 12 |
| `plot_compaction_contention.py` | the summary CSV of `run_corfu_compaction_contention.py` | (not in the paper) |
| `prices.json`, `space.json`, `space.example.json` | cost-model inputs; the same files sit in `figure12_cost/data/` | 12 |

Figures with no script here are produced entirely inside their figure folder: 1a, 1b, 2b
(RocksDB and SlateDB microbenchmarks), 16 (append/page-cache C++ probes), 17 and 18 (use
cases). Figure 10's `plot_throughput_writers.py` and Figure 2a's `plot_sqlite_workload.py`
are paper-specific plotters that also live only in their figure folders.
