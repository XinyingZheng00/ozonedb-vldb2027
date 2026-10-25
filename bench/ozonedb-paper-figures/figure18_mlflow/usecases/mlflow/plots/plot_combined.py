#!/usr/bin/env python3
"""Generate mlflow_combined.pdf, the Figure 18 of the paper. It shows
error rate, peak server-side RSS and training-compute time versus P in one
row of three panels. The panels share one x label and one legend.

The script was rebuilt to match the PDF of the paper (2026-08-30). The
original script of that PDF was not kept. The panel geometry, fonts,
markers and the small offsets were read back from that PDF. The offsets
keep overlapping lines visible ("Lines perturbed slightly for clarity" in
the caption). Only ozonedb, sqlite and postgres are drawn. The
postgres_serialized control is left out, as in the paper.

Usage:
    python3 plot_combined.py [--out PATH]
"""
import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plot_common import BACKEND_COLOR, all_ps, load_rows, series_with_error

HERE = Path(__file__).resolve().parent
DATA = HERE.parent.parent.parent / "data"

BACKENDS = ["ozonedb", "sqlite", "postgres"]
LABEL = {
    "ozonedb": "OzoneDB",
    "sqlite": "SQLite (MLflow default)",
    "postgres": "Postgres (client-server)",
}
MARKER = {"ozonedb": "o", "sqlite": "s", "postgres": "^"}

# (csv dir, field, scale, y label, y scale, per-backend offset in plotted units).
# The offsets lift lines that would otherwise sit exactly on top of each other.
# Every ozonedb/postgres error rate and every ozonedb/sqlite RSS value is 0.
PANELS = [
    ("ray_mlflow_swapin_3thread_5s_psweep", "error_rate", 100, "error rate (%)",
     "linear", {"ozonedb": 0.0015, "postgres": 0.003}),
    ("ray_mlflow_swapin_resource_usage", "postgres_peak_rss_mb", 1, "Peak RSS\nmemory (MB)",
     "symlog", {"sqlite": 0.8}),
    ("ray_mlflow_swapin_compute_time", "mean_compute_latency_ms", 1, "Compute time\nper step (ms)",
     "linear", {}),
]

# Page and panel geometry in points, as measured from the paper's PDF.
PAGE_W, PAGE_H = 1025.655, 247.295625
AX_X = [73.815, 432.1625409836, 764.5428688525]
AX_Y, AX_W, AX_H = 89.22, 259.6721311475, 156.24


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "mlflow_combined.pdf"))
    args = ap.parse_args()

    plt.rcParams.update({"font.size": 18, "axes.labelsize": 22,
                         "xtick.labelsize": 18, "ytick.labelsize": 18})
    fig = plt.figure(figsize=(PAGE_W / 72, PAGE_H / 72))
    axes = [fig.add_axes([x / PAGE_W, AX_Y / PAGE_H, AX_W / PAGE_W, AX_H / PAGE_H])
            for x in AX_X]

    for ax, (run, field, scale, ylabel, yscale, offset) in zip(axes, PANELS):
        rows = load_rows(DATA / run / "trials.csv")
        ps = all_ps(rows)
        for backend in BACKENDS:
            xs, ys, errs = series_with_error(rows, backend, field, ps)
            ys = [y * scale + offset.get(backend, 0) for y in ys]
            errs = [e * scale for e in errs]
            ax.errorbar(xs, ys, yerr=errs, marker=MARKER[backend], markersize=8.5,
                        linewidth=1.6, capsize=2.5, elinewidth=0.9,
                        color=BACKEND_COLOR[backend], label=LABEL[backend])
        ax.set_ylabel(ylabel, linespacing=1.296)  # line gap of the paper's two-line labels
        ax.set_xticks(range(len(ps)))
        ax.set_xticklabels([str(p) for p in ps])
        if yscale == "symlog":
            ax.set_yscale("symlog", linthresh=10)  # embedded stores have no server: 0 MB
            # Range of the panel in the paper. Otherwise newer matplotlib
            # would pad the symlog axis down into negative decades.
            ax.set_ylim(-2.0, 2722)
        ax.grid(True, which="both", axis="y", linewidth=0.4, alpha=0.5)
        ax.grid(True, which="major", axis="x", linewidth=0.4, alpha=0.3)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    axes[0].axhline(0, color="#3a3a38", linewidth=0.6, alpha=0.4)
    # Shared x label and legend, both centered at x = 505.8 pt as in the paper.
    fig.text(505.8 / PAGE_W, 46.38 / PAGE_H,
             "P (concurrent Ray Tune trial processes, 3 threads each)",
             ha="center", va="baseline", fontsize=22)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="center", ncol=3, frameon=False, fontsize=21,
               bbox_to_anchor=(505.82 / PAGE_W, 20.0 / PAGE_H),
               handlelength=1.4, handletextpad=0.35, columnspacing=0.70)
    fig.savefig(args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
