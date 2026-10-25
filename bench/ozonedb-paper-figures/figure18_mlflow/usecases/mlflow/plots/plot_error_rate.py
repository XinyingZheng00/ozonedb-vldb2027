#!/usr/bin/env python3
"""Generate error_rate.pdf from trials.csv. The plot shows the log_metric()
failure rate versus P, with a linear y-axis. The scale is linear, not log,
on purpose. Most points are exactly 0.0000. log(0) is undefined. A clip
of the zeros to some epsilon floor would visually overstate near-zero
error rates, as if they were real. This metric is "mostly zero, then a
real, small nonzero tail". A linear axis represents that honestly.

Usage:
    python3 plot_error_rate.py [--csv PATH] [--out PATH]
"""
import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plot_common import (BACKEND_COLOR, BACKEND_LABEL, BACKEND_LINESTYLE,
                          BACKEND_ORDER, all_ps, load_rows, series_with_error,
                          style_axes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "error_rate.pdf"))
    args = ap.parse_args()

    rows = load_rows(args.csv)
    ps = all_ps(rows)

    fig, ax = plt.subplots(figsize=(4.6, 3.4))
    for backend in BACKEND_ORDER:
        xs, ys, errs = series_with_error(rows, backend, "error_rate", ps)
        if not xs:
            continue
        # Percent, not fraction. On a linear axis shared with many exact zeros,
        # 0.0006 reads far more clearly as "0.06%" than as "6e-04".
        ys_pct = [y * 100 for y in ys]
        errs_pct = [e * 100 for e in errs]
        ax.errorbar(xs, ys_pct, yerr=errs_pct, marker="o", markersize=4, linewidth=1.6,
                     capsize=2.5, elinewidth=0.9, linestyle=BACKEND_LINESTYLE[backend],
                     color=BACKEND_COLOR[backend], label=BACKEND_LABEL[backend])

    style_axes(ax, ps, "log_metric() error rate (%)",
               "database is locked error rate vs. concurrency", logy=False)
    ax.axhline(0, color="#3a3a38", linewidth=0.6, alpha=0.4)
    ax.legend(fontsize=6.5, frameon=False, loc="best")
    fig.tight_layout()
    fig.savefig(args.out, bbox_inches="tight")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
