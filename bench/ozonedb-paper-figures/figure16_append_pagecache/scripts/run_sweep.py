#!/usr/bin/env python3
"""Run the Figure 16 sweep: {buffered, direct} x {fsync, none} x N, with
repeats. Write one CSV row per run, plus a summary CSV (mean, stdev).

    python3 run_sweep.py --dir /mnt/ext4/appendtest --out ../data/<run_name>

--dir must be on the filesystem under test (ext4 in the paper). Each run
truncates <dir>/append.dat, so keep nothing else there. With --drop-caches
(Linux, uses sudo), the script drops the page cache before every run.
"""
import argparse
import csv
import platform
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIELDS = ["mode", "sync", "procs", "record_bytes", "seconds", "total_ops",
          "ops_per_sec", "verified"]


def build():
    # One binary per platform. So the node never runs a copy that was built on a laptop.
    exe = HERE / f"append_bench.{platform.system().lower()}-{platform.machine()}"
    src = HERE / "append_bench.cpp"
    if not exe.exists() or exe.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(["c++", "-O2", "-std=c++17", "-Wall", "-o", str(exe), str(src)], check=True)
    return exe


def drop_caches():
    subprocess.run(["sudo", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, type=Path, help="directory on the filesystem under test")
    ap.add_argument("--out", required=True, type=Path, help="output directory for the CSVs")
    ap.add_argument("--procs", default="1,2,4,8,16,32")
    ap.add_argument("--modes", default="buffered,direct")
    ap.add_argument("--syncs", default="fsync,none")
    ap.add_argument("--duration", type=float, default=10)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--record", type=int, default=4096)
    ap.add_argument("--drop-caches", action="store_true")
    args = ap.parse_args()

    bench = [str(build())]
    args.dir.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.dir / "append.dat"
    runs_csv = args.out / "runs.csv"
    (args.out / "host.txt").write_text(
        f"{platform.platform()}\n{platform.processor()}\n"
        + subprocess.run(["df", "-hT" if sys.platform.startswith("linux") else "-h", str(args.dir)],
                         capture_output=True, text=True).stdout)

    rows = []
    with open(runs_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["repeat"] + FIELDS)
        for rep in range(args.repeats):
            for sync in args.syncs.split(","):
                for mode in args.modes.split(","):
                    for n in args.procs.split(","):
                        if args.drop_caches:
                            drop_caches()
                        cmd = bench + ["--path", str(target), "--mode", mode, "--sync", sync,
                               "--procs", n, "--duration", str(args.duration),
                               "--record", str(args.record), "--verify"]
                        out = subprocess.run(cmd, capture_output=True, text=True)
                        if out.returncode != 0:
                            print(f"FAILED: {' '.join(cmd)}\n{out.stderr}", file=sys.stderr)
                            continue
                        vals = out.stdout.strip().split(",")
                        w.writerow([rep] + vals)
                        f.flush()
                        rows.append(dict(zip(FIELDS, vals)))
                        print(f"rep {rep} {sync:5s} {mode:8s} N={n:>2}: "
                              f"{float(vals[6]):>10.0f} ops/s  verify={vals[7]}", flush=True)
    target.unlink(missing_ok=True)

    groups = defaultdict(list)
    for r in rows:
        groups[(r["sync"], r["mode"], int(r["procs"]))].append(float(r["ops_per_sec"]))
    with open(args.out / "summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sync", "mode", "procs", "repeats", "ops_per_sec_mean", "ops_per_sec_stdev"])
        for (sync, mode, n), v in sorted(groups.items()):
            w.writerow([sync, mode, n, len(v), round(statistics.mean(v), 1),
                        round(statistics.stdev(v), 1) if len(v) > 1 else 0.0])
    print(f"wrote {runs_csv} and {args.out / 'summary.csv'}")


if __name__ == "__main__":
    main()
