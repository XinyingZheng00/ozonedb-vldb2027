#!/usr/bin/env python3
"""Generate resource_usage.pdf: peak server-side RSS memory vs P, log-y,
from ray_mlflow_swapin_resource_usage/trials.csv.

sqlite/ozonedb are 0 by construction -- no separate server process exists
for them at all, not just "small" -- so their lines sit on the x-axis.
postgres/postgres_serialized show the real, measured peak RSS summed
across every real `postgres` OS process (background + per-connection
backends) during each trial. This is the resource cost a client-server
architecture pays that an embedded store structurally cannot.

Usage:
    python3 plot_resource_usage.py [--csv PATH] [--out PATH]
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

HERE = Path(__file__).resolve().parent
DEFAULT_CSV = (HERE.parent / "data" / "paper_data"
               / "ray_mlflow_swapin_resource_usage" / "trials.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--out", default=str(HERE.parent / "plots" / "resource_usage.pdf"))
    args = ap.parse_args()

    rows = load_rows(args.csv)
    ps = all_ps(rows)

    fig, ax = plt.subplots(figsize=(4.6, 3.4))
    for backend in BACKEND_ORDER:
        xs, ys, errs = series_with_error(rows, backend, "postgres_peak_rss_mb", ps)
        if not xs:
            continue
        ax.errorbar(xs, ys, yerr=errs, marker="o", markersize=4, linewidth=1.6,
                     capsize=2.5, elinewidth=0.9, linestyle=BACKEND_LINESTYLE[backend],
                     color=BACKEND_COLOR[backend], label=BACKEND_LABEL[backend])

    ax.set_xlabel("P (concurrent Ray Tune trial processes, 3 threads each)")
    ax.set_ylabel("peak server-side RSS memory (MB)")
    ax.set_title("Storage-server memory cost vs. concurrency")
    ax.set_xticks(range(len(ps)))
    ax.set_xticklabels([str(p) for p in ps])
    ax.set_yscale("symlog", linthresh=10)  # sqlite/ozonedb sit exactly at 0
    ax.grid(True, which="both", axis="y", linewidth=0.4, alpha=0.5)
    ax.grid(True, which="major", axis="x", linewidth=0.4, alpha=0.3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(fontsize=6.5, frameon=False, loc="best")
    fig.tight_layout()
    fig.savefig(args.out, bbox_inches="tight")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
