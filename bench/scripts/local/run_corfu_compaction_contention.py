"""Run one (writers, trial) iteration of the compaction-contention measurement on ozonedb-corfu.

run_ycsb_with_corfu.sh drives it:

  OZONEDB_RUN_TAG=compaction_sweep \\
    bench/scripts/local/run_ycsb_with_corfu.sh \\
      --script bench/scripts/local/run_corfu_compaction_contention.py \\
      --workloads a --writers-list "1 2 4 8 16" --trials 3

The shell wrapper restarts Corfu and iterates over (writers, trial). This
script runs one pair per invocation:

  1. Calls run_ycsb (workload A, ozonedb-corfu) with the corfu config tuned
     for frequent small compactions (corfu_compaction_experiment in ycsb.yaml).
  2. Scrapes the per-writer .result files for the [COMPACTION_NEEDED] and
     [COMPACTION_COMMITTED] lines the C++ instrumentation prints.
  3. Joins by task_id: t_needed = first NEEDED across writers;
     t_committed = last COMMITTED across writers (each peer prints it once
     when its rollforward applies the COMPACT record).
  4. Writes a per-(W, trial) CSV with one row per task and appends one
     summary row (mean, p50, p95, p99) to a run-tag-scoped summary CSV.

All writer processes share the host, so CLOCK_REALTIME is the same for every
PID. A multi-host version needs synced clocks and per-host offsets.

Plot with bench/scripts/plot/plot_compaction_contention.py.
"""

import argparse
import csv
import glob
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

import yaml

from run_local_ycsb_multiproc import run_ycsb
from load_local_ycsb_multiproc import (
    linearizable_corfu_settings,
    result_label,
)

# bench/scripts is one level up. ycsb_config.derive() resolves the `nodes:`
# block into the cloudlab.hosts, corfu.endpoint and s3.endpoint keys.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ycsb_config import derive as _derive_addresses

ozonedb_home = os.environ.get("OZONEDB_HOME")

NEEDED_RE = re.compile(
    r"\[COMPACTION_NEEDED\] task_id=(\S+) wall_ns=(\d+) writer_pid=(\d+)"
)
COMMITTED_RE = re.compile(
    r"\[COMPACTION_COMMITTED\] task_id=(\S+) wall_ns=(\d+) writer_pid=(\d+)"
)


def scrape(result_dir, writers, trial, engine="ozonedb-corfu"):
    """Walk the per-writer .result files for this (W, trial) and join task_ids.

    Returns ({task_id: earliest_needed_ns},
             {task_id: [committed_ns per writer_pid]}).
    One NEEDED per task_id is expected: the C++ gate excludes dead-task
    takeovers (owner_generation > 0).
    """
    needed = {}
    committed = defaultdict(list)
    pattern = os.path.join(
        result_dir, f"*-workloada-{engine}_w*of{writers}_*_trial{trial}.result"
    )
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"[scrape] WARNING: no result files matched {pattern}")
    for path in files:
        with open(path, "r", errors="replace") as fh:
            for line in fh:
                m = NEEDED_RE.search(line)
                if m:
                    tid, ns = m.group(1), int(m.group(2))
                    if tid not in needed or ns < needed[tid]:
                        needed[tid] = ns
                    continue
                m = COMMITTED_RE.search(line)
                if m:
                    committed[m.group(1)].append(int(m.group(2)))
    return needed, committed


def percentile(sorted_data, p):
    """Nearest-rank percentile. Returns 0.0 for an empty sample."""
    if not sorted_data:
        return 0.0
    n = len(sorted_data)
    idx = min(n - 1, max(0, int(p * n)))
    return sorted_data[idx]


def main():
    parser = argparse.ArgumentParser(
        description="One iteration of the compaction-contention experiment."
    )
    parser.add_argument(
        "--config",
        type=str,
        default=os.path.join(
            ozonedb_home or "", "bench/scripts/config/ycsb.yaml"
        ),
    )
    parser.add_argument("--num_writers", type=int, required=True)
    parser.add_argument("--trial", type=int, required=True)
    # Accepted for run_ycsb_with_corfu.sh compatibility. Always overridden to
    # "a": the measurement is workload-A specific.
    parser.add_argument("--workloads", type=str, default="a")
    parser.add_argument(
        "--linearizable",
        action="store_true",
        help="Force strict reads (linearizable_reads=true, "
             "trust_background_tail=false) into every writer config. Result "
             "files are labelled ozonedb-corfu-linearizable.",
    )
    parser.add_argument(
        "--max_exec_time",
        type=int,
        default=None,
        help="Cap each YCSB run at this many seconds (overrides local.run.max_exec_time).",
    )
    args = parser.parse_args()

    if not ozonedb_home:
        print("OZONEDB_HOME not set", file=sys.stderr)
        sys.exit(1)
    if args.workloads.strip() != "a":
        print(
            f"[contention] note: --workloads='{args.workloads}' overridden to 'a' "
            "(experiment is workload-A specific)"
        )

    with open(args.config, "r") as f:
        config = _derive_addresses(yaml.safe_load(f))

    run_cfg = config["local"]["run"]

    # Corfu settings: yaml.corfu (transport, endpoint) merged with
    # yaml.corfu_compaction_experiment (compaction tuning). Both reach
    # shared_config_w{i}.json through _make_corfu_config_per_writer.
    corfu_settings = dict(config.get("corfu") or {})
    overrides = config.get("corfu_compaction_experiment") or {}
    if not overrides:
        print(
            "[contention] WARNING: corfu_compaction_experiment block missing from "
            f"{args.config}; defaults from shared_config_base.json will produce "
            "very few compactions"
        )
    corfu_settings.update(overrides)
    if args.linearizable:
        corfu_settings = linearizable_corfu_settings(corfu_settings)
    # Derived from the effective settings, so linearizable_reads in ycsb.yaml
    # gets the same label as the flag. scrape() must use this token.
    engine = result_label("ozonedb-corfu", corfu_settings)
    s3_settings = config.get("s3")

    W = args.num_writers
    trial = args.trial
    max_exec_time = (
        args.max_exec_time
        if args.max_exec_time is not None
        else run_cfg.get("max_exec_time")
    )
    run_tag = os.environ.get("OZONEDB_RUN_TAG") or datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )
    result_dir = os.path.join(ozonedb_home, "bench/results/local", run_tag)

    print(
        f"[contention] starting: writers={W} trial={trial} run_tag={run_tag} "
        f"engine={engine} "
        f"corfu_overrides={sorted(overrides.keys())}"
    )

    run_ycsb(
        workload_names=["a"],
        record_cnt=run_cfg["record_cnt"],
        operation_cnts=run_cfg["operation_cnt"],
        key_size=run_cfg["key_size"],
        db_names=["ozonedb-corfu"],
        repeated=run_cfg["repeated"],
        ycsb_data_path=run_cfg["ycsb_data_path"],
        threads=run_cfg["threads"],
        num_writers=W,
        offset=0,
        total_writers=W,
        corfu_settings=corfu_settings,
        s3_settings=s3_settings,
        max_exec_time=max_exec_time,
        trial=trial,
    )

    needed, committed = scrape(result_dir, W, trial, engine)
    tasks = []
    for tid, t0 in needed.items():
        if tid not in committed:
            # The workload ended before all peers caught up: drop the task. If
            # this happens often, lengthen max_exec_time or shrink record_cnt.
            continue
        peer_times = committed[tid]
        t1 = max(peer_times)
        duration_ms = (t1 - t0) / 1e6
        tasks.append((tid, t0, t1, duration_ms, len(peer_times)))

    per_csv = os.path.join(
        result_dir, f"compaction_contention_w{W}_trial{trial}.csv"
    )
    os.makedirs(result_dir, exist_ok=True)
    with open(per_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            ["task_id", "t_needed_ns", "t_committed_ns", "duration_ms",
             "num_peers_committed"]
        )
        for row in tasks:
            w.writerow([row[0], row[1], row[2], f"{row[3]:.3f}", row[4]])
    print(
        f"[contention] wrote {per_csv}: {len(tasks)} joined / "
        f"{len(needed)} needed / {sum(len(v) for v in committed.values())} committed"
    )

    summary_csv = os.path.join(result_dir, "compaction_contention_summary.csv")
    write_header = not os.path.exists(summary_csv)
    durations = sorted(t[3] for t in tasks)
    n = len(durations)
    mean_ms = sum(durations) / n if n else 0.0
    with open(summary_csv, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(
                ["writer_count", "trial", "n_tasks", "mean_ms", "p50_ms",
                 "p95_ms", "p99_ms"]
            )
        w.writerow([
            W, trial, n,
            f"{mean_ms:.3f}",
            f"{percentile(durations, 0.50):.3f}",
            f"{percentile(durations, 0.95):.3f}",
            f"{percentile(durations, 0.99):.3f}",
        ])
    print(f"[contention] appended summary row to {summary_csv}")


if __name__ == "__main__":
    main()
