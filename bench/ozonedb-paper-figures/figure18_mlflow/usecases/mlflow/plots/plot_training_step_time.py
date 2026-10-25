#!/usr/bin/env python3
"""Generate training_step_time.pdf: pure training-compute time versus P,
with a log y-axis, from ray_mlflow_swapin_compute_time/trials.csv. The
time is the mean of partial_fit(), predict_proba() and log_loss() per
step, in ms.

This code path never references the storage backend at all. The sklearn
and numpy work is identical, regardless of which backend the trial uses.
Any difference across backends here is host resource contention: the own
OS processes of a client-server backend compete for CPU with the training
processes. It is not a difference in the training code itself.

Usage:
    python3 plot_training_step_time.py [--csv PATH] [--out PATH]
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
DEFAULT_CSV = (HERE.parent.parent.parent / "data"
               / "ray_mlflow_swapin_compute_time" / "trials.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--out", default=str(HERE / "training_step_time.pdf"))
    args = ap.parse_args()

    rows = load_rows(args.csv)
    ps = all_ps(rows)

    fig, ax = plt.subplots(figsize=(4.6, 3.4))
    for backend in BACKEND_ORDER:
        xs, ys, errs = series_with_error(rows, backend, "mean_compute_latency_ms", ps)
        if not xs:
            continue
        ax.errorbar(xs, ys, yerr=errs, marker="o", markersize=4, linewidth=1.6,
                     capsize=2.5, elinewidth=0.9, linestyle=BACKEND_LINESTYLE[backend],
                     color=BACKEND_COLOR[backend], label=BACKEND_LABEL[backend])

    style_axes(ax, ps, "training-compute time per step (ms, log scale)",
               "Training-compute time vs. concurrency\n(partial_fit/predict_proba/log_loss -- never touches storage)")
    ax.legend(fontsize=6.5, frameon=False, loc="best")
    fig.tight_layout()
    fig.savefig(args.out, bbox_inches="tight")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
