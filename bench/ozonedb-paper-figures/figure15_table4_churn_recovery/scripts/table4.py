#!/usr/bin/env python3
"""Compute Table 4 (average reassignments per abort ratio) from a churn run.

Each value is the mean of n_tasks_with_reassignment over the trials at that
abort ratio (the 'vary abort' rows of trials.csv).

    python3 table4.py <run>/trials.csv [--out FILE]     # default: print to stdout
"""
import argparse
import collections
import csv
import statistics
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trials_csv", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="write the table here instead of stdout")
    args = ap.parse_args()

    by_rate = collections.defaultdict(list)
    with open(args.trials_csv, newline="") as f:
        for r in csv.DictReader(f):
            if r["vary"] == "abort":
                by_rate[float(r["abort_rate"])].append(int(r["n_tasks_with_reassignment"]))
    if not by_rate:
        sys.exit(f"{args.trials_csv} has no 'vary abort' trials")

    lines = ["abort_ratio\tavg_reassignments"]
    lines += [f"{a:.0%}\t{statistics.mean(by_rate[a]):.1f}" for a in sorted(by_rate)]
    text = "\n".join(lines) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
