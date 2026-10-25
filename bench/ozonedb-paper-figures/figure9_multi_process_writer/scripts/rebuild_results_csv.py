#!/usr/bin/env python3
"""Rebuild one results.csv from the per-cell {system}_workload{w}_t{T}_r{R}.log
files that run_fig2_sweep.py writes.

The orchestrator overwrites results.csv on every invocation. A chained
two-phase sweep (T={2,4,8,16}, then T=1) therefore leaves only the second
phase in the CSV. This rebuild parses the `Run throughput(ops/sec)` line and
the /usr/bin/time RSS from every .log file. It then aggregates the values per
(system, workload, threads) across repeats. The output has the same schema
that run_fig2_sweep.py emits.
"""

from __future__ import annotations

import argparse
import os
import re
import statistics
from collections import defaultdict
from pathlib import Path

OZONEDB_HOME = Path(os.environ.get("OZONEDB_HOME", Path.home() / "ozonedb"))
RESULTS_LOCAL = OZONEDB_HOME / "bench" / "results" / "local"

LOG_RE = re.compile(r"^(?P<sys>[a-z0-9]+)_workload(?P<wl>[a-z])_t(?P<t>\d+)_r(?P<r>\d+)\.log$")
# YCSB-cpp emits "Run throughput(ops/sec): N". YCSB-Java (OzoneDB) emits
# "[OVERALL], Throughput(ops/sec), N". The pattern matches either. N can be in
# scientific notation (for example 1.11796e+06) for throughputs of 1M ops/sec or more.
_NUM = r"[\d.]+(?:[eE][+-]?\d+)?"
THROUGHPUT_RE = re.compile(
    rf"(?:Run throughput\(ops/sec\):\s*|^\[OVERALL\],\s+Throughput\(ops/sec\),\s+)({_NUM})",
    re.MULTILINE,
)
RSS_RE = re.compile(r"Maximum resident set size \(kbytes\):\s*(\d+)")
SECTORS_BEFORE_RE = re.compile(r"sectors_before=(\d+)")
SECTORS_AFTER_RE = re.compile(r"sectors_after=(\d+)")


def parse_log(path: Path) -> dict | None:
    text = path.read_text(errors="replace")
    m = THROUGHPUT_RE.search(text)
    if not m:
        return None  # failed run, no throughput reported
    out = {"throughput": float(m.group(1))}
    if (rss := RSS_RE.search(text)):
        out["max_rss_kb"] = int(rss.group(1))
    sb = SECTORS_BEFORE_RE.search(text)
    sa = SECTORS_AFTER_RE.search(text)
    if sb and sa:
        out["bytes_written"] = (int(sa.group(1)) - int(sb.group(1))) * 512
    return out


def _write_csv(out_path: Path, rows: list) -> None:
    sys_order = {"ozonedb": 0, "rocksdb": 1, "trunkcpp": 2, "bcw2": 3, "hctree": 4}
    rows.sort(key=lambda r: (sys_order.get(r[0], 99), r[1], r[2]))
    with out_path.open("w") as f:
        f.write("system,workload,threads,n_repeats,throughput_mean,throughput_stddev,"
                "rel_stddev_pct,max_rss_kb_mean,bytes_written_mean,failures\n")
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")


def _aggregate(cells: dict) -> list:
    rows = []
    for (sysname, wl, t), reps in sorted(cells.items()):
        ts = [r["throughput"] for r in reps]
        rss = [r["max_rss_kb"] for r in reps if "max_rss_kb" in r]
        bw = [r["bytes_written"] for r in reps if "bytes_written" in r]
        n = len(ts)
        mean = statistics.mean(ts) if n else ""
        sd = statistics.stdev(ts) if n >= 2 else ""
        rel = (100.0 * sd / mean) if (sd != "" and mean) else ""
        rss_mean = statistics.mean(rss) if rss else ""
        bw_mean = statistics.mean(bw) if bw else ""
        rows.append((sysname, wl, t, n, mean, sd, rel, rss_mean, bw_mean, ""))
    return rows


def _scan_dir(dir_path: Path) -> tuple[dict, int]:
    cells: dict[tuple[str, str, int], list[dict]] = defaultdict(list)
    failed = 0
    for log in sorted(dir_path.glob("*.log")):
        m = LOG_RE.match(log.name)
        if not m:
            continue  # FAILED logs and sweep.log
        parsed = parse_log(log)
        if parsed is None:
            failed += 1
            continue
        key = (m.group("sys"), m.group("wl"), int(m.group("t")))
        cells[key].append(parsed)
    return cells, failed


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--fig1-dir", type=Path,
                   default=RESULTS_LOCAL / "fig1-single-writer-local",
                   help="Single-writer (T=1) results directory")
    p.add_argument("--fig2-dir", type=Path,
                   default=RESULTS_LOCAL / "fig2-multi-writer-local",
                   help="Multi-writer (T>1) results directory")
    args = p.parse_args()

    # Scan each directory and write its own CSV. Each CSV contains every cell
    # whose log files live in that directory. The chain script and
    # run_fig2_sweep.py --results-dir enforce the convention. Under it, fig1
    # gets the T=1 rows and fig2 gets the T>1 rows.
    for label, d in [("fig1", args.fig1_dir), ("fig2", args.fig2_dir)]:
        if not d.exists():
            print(f"  {label}: skipped (dir does not exist)")
            continue
        cells, failed = _scan_dir(d)
        rows = _aggregate(cells)
        out = d / "results.csv"
        _write_csv(out, rows)
        print(f"  {label}: wrote {len(rows)} rows to {out}  "
              f"(cells={len(cells)}, failed-log={failed})")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
