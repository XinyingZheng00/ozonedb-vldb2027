#!/usr/bin/env python3
"""Aggregate a phase's trials.csv (multiple repeats per configuration) into
mean +/- stddev per configuration, plus min/max so a reviewer can see the
actual spread, not just a smoothed average.

Groups by every column except a fixed set of per-trial metric columns and
"repeat" itself, so this works unmodified across Phase 1/2/3's CSVs (they
share the same shape: some key columns + repeat + metric columns).
"""
import argparse
import csv
import statistics
import sys
from pathlib import Path

METRIC_COLS = {
    "repeat", "wall_s", "n_distinct_pids", "n_cli_spawned",
    "total_ops", "ok_ops", "failed_ops", "error_rate", "error_counts",
    "throughput_ops_sec", "successful_throughput_ops_sec", "throughput_cycles_sec",
    "p50_latency_ms", "p95_latency_ms", "p99_latency_ms", "mean_latency_ms",
    "verify_checked", "verify_mismatches", "verify_missing_present", "verify_unexpected_present",
}


def fnum(v):
    if v is None or v == "" or v == "None":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--metrics", default="throughput_ops_sec,throughput_cycles_sec,error_rate,p50_latency_ms,p99_latency_ms",
                     help="comma-separated metric columns to aggregate (only ones present in the file are used)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.csv_path)))
    if not rows:
        print("no rows", file=sys.stderr)
        sys.exit(1)

    key_cols = [c for c in rows[0].keys() if c not in METRIC_COLS]
    wanted_metrics = [m for m in args.metrics.split(",") if m in rows[0].keys()]

    groups = {}
    for row in rows:
        key = tuple(row[c] for c in key_cols)
        groups.setdefault(key, []).append(row)

    out_rows = []
    for key, grp in sorted(groups.items()):
        out = dict(zip(key_cols, key))
        out["n_repeats"] = len(grp)
        # also carry verify totals summed across repeats (correctness should be checked over ALL repeats, not averaged)
        vmis = sum(int(r.get("verify_mismatches", 0) or 0) for r in grp)
        vchk = sum(int(r.get("verify_checked", 0) or 0) for r in grp)
        if "verify_checked" in rows[0]:
            out["verify_mismatches_total"] = vmis
            out["verify_checked_total"] = vchk
        for m in wanted_metrics:
            vals = [fnum(r.get(m)) for r in grp]
            vals = [v for v in vals if v is not None]
            if not vals:
                out[f"{m}_mean"] = out[f"{m}_std"] = out[f"{m}_min"] = out[f"{m}_max"] = None
                continue
            out[f"{m}_mean"] = statistics.mean(vals)
            out[f"{m}_std"] = statistics.stdev(vals) if len(vals) > 1 else 0.0
            out[f"{m}_min"] = min(vals)
            out[f"{m}_max"] = max(vals)
        out_rows.append(out)

    out_path = args.out or str(Path(args.csv_path).with_name("trials_aggregated.csv"))
    fieldnames = list(out_rows[0].keys())
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in out_rows:
            w.writerow(r)

    print(f"wrote {out_path} ({len(out_rows)} configurations x up to {max(o['n_repeats'] for o in out_rows)} repeats)")
    for out in out_rows:
        key_str = " ".join(f"{c}={out[c]}" for c in key_cols)
        metric_str = "  ".join(
            f"{m}={out[f'{m}_mean']:.3f}±{out[f'{m}_std']:.3f} [{out[f'{m}_min']:.3f},{out[f'{m}_max']:.3f}]"
            for m in wanted_metrics if out.get(f"{m}_mean") is not None
        )
        verify_str = ""
        if "verify_mismatches_total" in out:
            verify_str = f"  verify={out['verify_mismatches_total']}/{out['verify_checked_total']}"
        print(f"{key_str} (n={out['n_repeats']}):  {metric_str}{verify_str}")


if __name__ == "__main__":
    main()
