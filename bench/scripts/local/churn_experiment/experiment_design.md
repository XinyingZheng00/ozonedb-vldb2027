# Churn experiment for OzoneKV: fixed-backlog completion time

## What the experiment measures

OzoneKV starts with a fixed compaction backlog. The append-based churn
recovery protocol (Section 4.4 of the paper) must complete that backlog under
churn. The experiment checks three properties:

1. **Liveness**: every compaction task in the backlog, and every cascade
   compaction it triggers, reaches a `TASK_COMPLETE` record.
2. **Correctness**: after completion, every key written before the experiment
   is readable with the value last assigned to it, at every abort rate.
3. **Bounded scaling**: completion time decreases, or at worst stays flat, as
   the writer count `N` grows. Completion time increases with the abort rate
   `R`, bounded by `max_generation × T_d` per task.

### Pass criteria

- Integrity pass rate = 100% across all conditions.
- The completion step converges within `completion_timeout_s` for every condition.
- Completion time increases monotonically with `R` (within single-trial noise).

## Cluster setup

Single host. All writers are local OS processes. Each one loads
`libozonedb.so` through JNI and opens the same on-disk DB directory. "Node
churn" maps to "compaction returns failure": the protocol cannot tell an
aborted compaction from one whose owner crashed mid-flight.

| Parameter | Value | Notes |
| --- | --- | --- |
| Records | 50,000 × 1 KB | Loaded once with compaction disabled, then snapshotted. |
| Writers `N` | varied ∈ {1, 2, 4, 8} (when `--vary writers`) or fixed at 4 (when `--vary abort`) | All read `compaction_abort_rate` from their shared config. |
| Abort rate `R` | varied ∈ {0%, 10%, 20%, 30%, 50%} (when `--vary abort`) or fixed at 0% (when `--vary writers`) | Per-compaction probability of an early return without `TASK_COMPLETE`. |
| Completion timeout | 600 s per trial | Hard cap on the completion step. |
| Quiescence grace | 8 s | Completion is "done" when every task has a `COMPLETE` record and `n_tasks_total` has been flat for 8 s. |
| Storage config | `log_file=1 MB`, `level_size=[16 MB, 256 MB]`, `level_file_size_limit=[32 MB, 256 MB]`, `max_level=2` | The last-level limit is large enough that last-level compactions always merge into one output, so the cascade converges. |
| `task_heartbeat_threshold` | 30 ticks ≈ 3 s `T_d` | See the engine behaviour below. |

### Engine behaviour the experiment depends on

1. **`task_heartbeat_threshold`** in `src/include/ozonedb/task_log_handler.h`
   is `30` ticks. The dead-task path must fire inside the experiment window.

2. **`COMPACT_START` stdout marker** at the entry of `doCompactionWork` in
   `src/db/compaction.cpp`. The runner counts compactions per trial with it.
   The `Compacting...` line prints at the *end* of the function.

3. **Dead-task priority (Step 0) in `pickCompaction`**. Under a continuous
   workload the live-work steps always find work, so a dead-task check after
   them never runs.

4. **`compaction_abort_rate` config key**, read in `doCompactionWork`. With
   probability `R` it sets the heartbeat thread's `aborted` flag and returns
   `kFailure` at once. The heartbeat thread sees the flag and exits without a
   `TASK_COMPLETE`. The task is abandoned exactly as if the writer had crashed
   mid-compaction. Off at the default of 0.

5. **`rollForwardTaskLog()` called from `pickCompaction`**. A quiescent writer
   with an empty live-work view then still replays the task log, so the
   heartbeat-timeout check runs and dead tasks are found.

6. **`find()` guard in `rollforwardSingleOperationRecord`**
   (`src/db/metadata_log_handler.cpp`). The last-level COMPACT path and the
   other path both check `find()` first. An input file that is not in the
   layout is skipped with a warning. Without the guard, `erase(end())` and
   `pop_front()` on an empty deque crash under heavy cascade churn.

The shared library is rebuilt after a change with:

```bash
cd $OZONEDB_HOME/build && cmake --build . -j
cp libOzoneDB.so $OZONEDB_HOME/ozonedb-jni-maven/native/src/main/cpp/lib/
cd $OZONEDB_HOME/ozonedb-jni-maven && mvn clean package
sudo cp jni/target/classes/libozonedb.so /usr/lib/
```

## Per-trial flow

1. **One-time setup (shared by all trials of a run)**: load 50 k records in
   one process with `"compaction_enabled": false` (skips
   `startCompactionWatcher`). Result: 39 sealed log files, 0 SSTables, empty
   `task.log`. The snapshot is kept at
   `$OZONEDB_DATA_ROOT/churn/snapshot_loaded_no_compaction/` and copied into
   each trial.

2. **Per-trial DB**: `cp -r --reflink=auto` the snapshot to a per-trial
   working dir.

3. **Completion step (measured)**: start `N` writers with
   `"compaction_enabled": true` and `"compaction_abort_rate": R` in their
   shared config, all running YCSB workload-c (read-only). Their
   `CompactionWatcher`s find the log backlog and start compacting; each
   successful log compaction triggers cascade work in level 1 and level 2.
   The runner polls `task.log` once per second. The trial is quiescent when
   every task has at least one `COMPLETE` record and `n_tasks_total` has been
   flat for 8 s. The runner then SIGTERMs the writers; their `closeDB` joins
   the in-flight compaction.

4. **Integrity check**: single-process YCSB workload-c with
   `requestdistribution=sequential`, `operationcount=50000`. Reads every
   loaded key once. `integrity_pass` holds iff `Return=OK == 50000`.

5. **Tear down**: wipe the per-trial DB dir.

## Metrics (per trial, into `trials.csv`)

| Metric | Definition |
| --- | --- |
| `vary` | "writers" or "abort": the parameter this trial varies. |
| `n_writers` | Writer count of this trial. |
| `abort_rate` | Configured `R` of this trial. |
| `completion_time_s` | Wall-clock of the completion step, including the 8 s grace tail. |
| `completed` | bool: did all tasks reach COMPLETE before the completion timeout? |
| `n_tasks_total` | Unique TaskIDs in `task.log` at the end of the completion step. |
| `n_tasks_with_reassignment` | Tasks that needed at least one reclaim (`max_generation > 0`). |
| `max_generation_observed` | Highest generation any task reached (1 = aborted once and recovered). |
| `generation_distribution` | Full histogram, e.g. `0:23;1:5;2:1`. |
| `integrity_pass` | bool: did the post-completion sequential read return OK for all 50 k keys? |
| `notes` | Free-form annotations (timeouts, anomalies). |

## Reproduction

```bash
cd $OZONEDB_HOME
# (one-time) rebuild, see above.
python3 bench/scripts/local/churn_experiment/churn_runner.py
python3 bench/scripts/local/churn_experiment/make_plots.py \
    bench/scripts/local/churn_experiment/results/v4_<timestamp>/trials.csv
```

CLI options of `churn_runner.py`:

- `--vary writers|abort|both`: which parameter to vary (default `both`).
- `--writers 1,2,4,8`: N values when `--vary=writers`.
- `--abort-rates 0.0,0.10,0.20,0.30,0.50`: R values when `--vary=abort`.
- `--fixed-r 0.0`: R held constant when `--vary=writers`.
- `--fixed-n 4`: N held constant when `--vary=abort`.
- `--rebuild-snapshot`: force a new priming load.
- `--record-count 50000`: dataset size.

## Output artifacts

```
churn_experiment/
├── experiment_design.md          ← this file
├── FINAL_REPORT.md               ← results + analysis
├── churn_runner.py               ← orchestrator
├── make_plots.py                 ← results.csv → PDFs
├── task_log_parser.py            ← shared task.log parser
├── churn_config.json             ← shared OzoneDB config
└── results/v4_<timestamp>/       ← (record_pb2.py is generated into
                                     build/generated/python on first use)
    ├── trials.csv, run_config.json
    ├── per_trial/<trial_id>/{load,writer_*,verify}.log
    │                          task_log_timeline.json
    └── plots/
        ├── completion_time_vs_writers.pdf
        ├── completion_time_vs_abort.pdf
        ├── max_generation.pdf
        └── generation_dist.pdf
```
