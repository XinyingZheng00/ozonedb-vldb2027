# Churn-tolerance experiment for OzoneDB — fixed-backlog completion-time

## Hypothesis

When OzoneDB has a fixed compaction backlog, the append-based churn-recovery
protocol completes the backlog correctly under churn. The protocol is in
Section 4.4 of the paper. The hypothesis has three parts:

1. **Liveness**. Every compaction task in the backlog reaches a
   `TASK_COMPLETE` record in the end. Every cascade compaction that a task
   triggers also reaches a `TASK_COMPLETE` record.
2. **Correctness**. After completion, every key that was written before the
   experiment is still readable. Each key returns the last value that was
   assigned to it. This holds for every per-compaction abort rate.
3. **Bounded scaling**. As the writer count `N` grows, the completion time
   decreases or, at worst, stays flat. As the abort rate `R` grows, the
   completion time increases. The per-task bound is `max_generation × T_d`.

### Success criteria

- The integrity pass rate is 100% for all conditions.
- The completion phase converges within `completion_timeout_s` for every condition.
- The completion time increases monotonically with `R`, within single-trial noise.

## Cluster setup

The experiment runs on a single host. All writers are local OS processes.
Each writer loads `libozonedb.so` through JNI and opens the same on-disk DB
directory. "Node churn" maps to "compaction returns failure". The protocol
cannot tell the difference between an aborted compaction and a compaction
whose owner crashed before it finished.

| Parameter | Value | Notes |
| --- | --- | --- |
| Records | 50,000 × 1 KB | The runner loads them once with compaction disabled, then takes a snapshot. |
| Writers `N` | varied ∈ {1, 2, 4, 8} with `--vary writers`, or fixed at 4 with `--vary abort` | All writers read `OZONEDB_CHURN_ABORT_RATE` from the environment. |
| Abort rate `R` | varied ∈ {0%, 10%, 20%, 30%, 50%} with `--vary abort`, or fixed at 0% with `--vary writers` | The probability that a compaction returns early and does not write `TASK_COMPLETE`. |
| Completion timeout | 600 s per trial | The hard cap on Phase B. |
| Quiescence grace | 8 s | The orchestrator declares completion "done" when every task has a `COMPLETE` record and `n_tasks_total` has been flat for 8 s. |
| Storage config | `log_file=1 MB`, `level_size=[16 MB, 256 MB]`, `level_file_size_limit=[32 MB, 256 MB]`, `max_level=2` | The last-level limit is large enough that last-layer compactions always merge into a single output. Because of this, the cascade converges. |
| `task_heartbeat_threshold` | 30 ticks ≈ 3 s `T_d` (patched) | See the deviations below. |

### Patches & rebuild (deviations from as-shipped code)

There are six source-level changes from the as-shipped code. Four are real
bug fixes. Two are experimental knobs.

1. **`task_heartbeat_threshold`** in
   `src/include/ozonedb/task_log_handler.h:61` changed from `10000000` to
   `30`. This change is required. The as-shipped value sets the timeout to
   about 11.6 days. With that value, the dead-task path cannot fire in any
   practical experiment window.

2. **Early `COMPACT_START` stdout marker** at the entry of
   `doCompactionWork` in `src/db/compaction.cpp`. The runner uses this
   marker to count compactions per trial. The original `Compacting...`
   line fires at the *end* of the function. For this reason, the
   experiment cannot use it for instrumentation.

3. **Step 0 dead-task priority** in `pickCompaction`. This change is
   required. Under a continuous workload, Steps 1–3 always find live work.
   The dead-task path then starves indefinitely.

4. **`OZONEDB_CHURN_ABORT_RATE` env knob** in `doCompactionWork`. With
   probability `R`, the function sets the heartbeat thread's `aborted`
   flag and returns `kFailure` immediately. The heartbeat thread checks
   the flag and exits. It does not write `TASK_COMPLETE`. The task is then
   abandoned, exactly as if the writer had crashed during the compaction.
   If the environment variable is not set, the knob does nothing.

5. **`rollForwardTaskLog()` called from `pickCompaction`**. In the
   as-shipped code, rollforward ran only inside `shouldWorkOnTask`. That
   is, it ran only when a writer actively tried to claim a task. A
   quiescent writer, for example one whose live-work view is empty, never
   called rollforward. So the heartbeat-timeout check inside rollforward
   never ran. So the writer never discovered dead tasks.

6. **Defensive `find()`-first in `rollforwardSingleOperationRecord`** at
   `src/db/metadata_log_handler.cpp:152-209`. The last-level COMPACT path
   crashed on `erase(end())`. The non-last-level path crashed on
   `pop_front()` from an empty deque, or popped the wrong element. Both
   paths now call `find()` first. If the input file is not in the layout,
   the path skips the record and writes a warning. This fixes a SIGSEGV in
   `__copy_move_backward_a1<...basic_string...>` that fired under
   high-cascade churn.

After any patch, rebuild the shared library with the standard two-step:

```bash
cd $OZONEDB_HOME/build && cmake --build . -j
cp libOzoneDB.so $OZONEDB_HOME/ozonedb-jni-maven/native/src/main/cpp/lib/
cd $OZONEDB_HOME/ozonedb-jni-maven && mvn clean package
sudo cp jni/target/classes/libozonedb.so /usr/lib/
```

## Per-trial flow

1. **One-time setup (shared across all trials in the run)**: a single
   process loads 50 k records with `compaction_policy=1`. This setting
   skips `startCompactionWatcher`. The result is 39 sealed log files, 0
   sstables, and an empty `task.log`. The runner saves the snapshot at
   `/tank/test/churn_v4/snapshot_loaded_no_compaction/`. It copies the
   snapshot into each trial with `cp`.

2. **Per-trial DB**: the runner copies the snapshot to a per-trial working
   dir with `cp -r --reflink=auto`.

3. **Completion phase (measured)**: the runner spawns `N` writers with
   `compaction_policy=0` (compaction enabled) and
   `OZONEDB_CHURN_ABORT_RATE=R`. All writers run YCSB workload-c
   (read-only). Their `CompactionWatcher`s discover the log backlog and
   start to compact. Each successful log compaction triggers cascade work
   in level 1 and level 2. The orchestrator polls `task.log` once per
   second. It declares the run quiescent when every task has at least one
   `COMPLETE` record and `n_tasks_total` has been flat for 8 s. Then it
   sends SIGTERM to the writers, whose `closeDB` joins the in-flight
   compaction.

4. **Integrity check**: a single process runs YCSB workload-c with
   `requestdistribution=sequential` and `operationcount=50000`. It reads
   every loaded key once. `integrity_pass` is true if, and only if,
   `Return=OK == 50000`.

5. **Tear down**: the runner wipes the per-trial DB dir.

## Metrics (per trial, into `trials.csv`)

| Metric | Definition |
| --- | --- |
| `vary` | "writers" or "abort". The parameter that this trial varies. |
| `n_writers` | The writer count for this trial. |
| `abort_rate` | The configured `R` for this trial. |
| `completion_time_s` | The wallclock time for Phase B. It includes the 8 s grace tail. |
| `completed` | bool. True if all tasks reached COMPLETE before the completion timeout. |
| `n_tasks_total` | The number of unique TaskIDs in `task.log` at the end of completion. |
| `n_tasks_with_reassignment` | The number of tasks that needed at least one reclaim (`max_generation > 0`). |
| `max_generation_observed` | The highest generation that any task reached. A value of 1 means the task aborted once and recovered. |
| `generation_distribution` | The full histogram, for example `0:23;1:5;2:1`. |
| `integrity_pass` | bool. True if the post-completion sequential read returned OK for all 50 k keys. |
| `notes` | Free-form annotations, for example timeouts and anomalies. |

## Reproduction

```bash
cd $OZONEDB_HOME
# One time only: apply the six source patches and rebuild. See Patches above.
python3 bench/scripts/local/churn_experiment/churn_runner.py
python3 bench/scripts/local/churn_experiment/make_plots.py \
    bench/scripts/local/churn_experiment/results/v4_<timestamp>/trials.csv
```

CLI options on `churn_runner.py`:

- `--vary writers|abort|both`: the parameter to vary. The default is `both`.
- `--writers 1,2,4,8`: the N values when `--vary=writers`.
- `--abort-rates 0.0,0.10,0.20,0.30,0.50`: the R values when `--vary=abort`.
- `--fixed-r 0.0`: the R that stays constant when `--vary=writers`.
- `--fixed-n 4`: the N that stays constant when `--vary=abort`.
- `--rebuild-snapshot`: forces the runner to run the priming load again.
- `--record-count 50000`: the dataset size.

## Output artifacts

```
churn_experiment/
├── experiment_design.md          ← this file
├── churn_runner.py               ← orchestrator
├── make_plots.py                 ← results.csv → PDFs
├── task_log_parser.py            ← shared task.log parser
├── churn_config.json             ← shared OzoneDB config
├── record_pb2.py                 ← generated protobuf (TaskRecord)
└── results/v4_<timestamp>/
    ├── trials.csv, run_config.json
    ├── per_trial/<trial_id>/{load,writer_*,verify}.log
    │                          task_log_timeline.json
    └── plots/
        ├── completion_time_vs_writers.pdf
        ├── completion_time_vs_abort.pdf
        ├── max_generation.pdf
        └── generation_dist.pdf
```
