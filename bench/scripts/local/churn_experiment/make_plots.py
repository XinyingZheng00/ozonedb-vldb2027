#!/usr/bin/env python3
"""Plot the churn experiment from a trials.csv (paper Table 4 and Fig 15).

The `vary` column splits the rows into the writers sweep and the abort sweep:
  results/v4_<ts>/plots/completion_time_vs_writers.pdf   (vary=writers)
  results/v4_<ts>/plots/completion_time_vs_abort.pdf     (vary=abort)
  results/v4_<ts>/plots/max_generation.pdf          (both)
  results/v4_<ts>/plots/generation_dist.pdf         (both)

The runner declares a trial complete only after n_total has been flat for
8 s. The plots mark that 8 s grace floor, and the actual completion time is
completion_time_s minus 8 s.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


QUIESCENCE_GRACE_S = 8.0


def to_float(s: str) -> float | None:
    if s == "" or s.lower() == "none":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load_trials(csv_path: Path) -> list[dict]:
    with csv_path.open() as f:
        return list(csv.DictReader(f))


def parse_gen_dist(s: str) -> dict[int, int]:
    out = {}
    if not s:
        return out
    for tok in s.split(";"):
        if ":" not in tok:
            continue
        k, v = tok.split(":")
        try:
            out[int(k)] = int(v)
        except ValueError:
            continue
    return out


# -----------------------------------------------------------------------------
# Plot 1: completion time vs # writers (vary=writers)
# -----------------------------------------------------------------------------


def plot_completion_vs_writers(rows: list[dict], out: Path) -> None:
    rows = [r for r in rows if r["vary"] == "writers"]
    rows.sort(key=lambda r: int(r["n_writers"]))
    if not rows:
        return
    xs = [int(r["n_writers"]) for r in rows]
    ys_total = [to_float(r["completion_time_s"]) or 0 for r in rows]
    ys_actual = [max(0.001, y - QUIESCENCE_GRACE_S) for y in ys_total]
    # Compaction speed: records × 1 KB, in MB, divided by the actual completion time.
    ys_speed = [
        (int(r["record_count"]) / (1024.0)) / t
        for r, t in zip(rows, ys_actual)
    ]

    # Paper figure fonts; the y labels take two lines to fit the short height.
    LABEL_SIZE, TICK_SIZE, PANEL_SIZE = 17, 16, 17
    fig, axes = plt.subplots(1, 2, figsize=(9.0, 2.45))
    ax_t, ax_s = axes

    ax_t.plot(xs, ys_actual, "s-", color="#ff7f0e", label="actual completion time")
    ax_t.set_xscale("log", base=2)
    ax_t.set_xticks(xs)
    ax_t.set_xticklabels([str(x) for x in xs])
    ax_t.set_xlabel("Number of Writers", fontsize=LABEL_SIZE)
    ax_t.set_ylabel("Completion\ntime (s)", fontsize=LABEL_SIZE)
    ax_t.tick_params(axis="both", labelsize=TICK_SIZE)
    ax_t.grid(True, linestyle=":", alpha=0.5)

    ax_s.plot(xs, ys_speed, "o-", color="#1f77b4", label="compaction speed")
    ax_s.set_xscale("log", base=2)
    ax_s.set_xticks(xs)
    ax_s.set_xticklabels([str(x) for x in xs])
    ax_s.set_xlabel("Number of Writers", fontsize=LABEL_SIZE)
    ax_s.set_ylabel("Compaction\nspeed (MB/s)", fontsize=LABEL_SIZE)
    ax_s.tick_params(axis="both", labelsize=TICK_SIZE)
    ax_s.grid(True, linestyle=":", alpha=0.5)

    # Panel labels in a thin band below the plots. No title: the paper
    # caption covers it.
    fig.tight_layout(rect=[0, 0.10, 1, 1.0])
    for ax, label in ((ax_t, "(a)"), (ax_s, "(b)")):
        bbox = ax.get_position()
        fig.text((bbox.x0 + bbox.x1) / 2, 0.01, label,
                 ha="center", va="bottom", fontsize=PANEL_SIZE)
    # bbox_inches crops the margins that the fixed rect leaves at the edges.
    fig.savefig(out, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


# -----------------------------------------------------------------------------
# Plot 2: completion time vs abort rate (vary=abort)
# -----------------------------------------------------------------------------


def plot_completion_vs_abort(rows: list[dict], out: Path) -> None:
    rows = [r for r in rows if r["vary"] == "abort"]
    if not rows:
        return

    from collections import defaultdict
    groups: dict[float, list[dict]] = defaultdict(list)
    for r in rows:
        groups[float(r["abort_rate"])].append(r)
    rates = sorted(groups.keys())

    xs = [100 * r for r in rates]
    means_total: list[float] = []
    lows_total: list[float] = []
    highs_total: list[float] = []
    means_actual: list[float] = []
    max_gens: list[int] = []
    for rate in rates:
        vals = [to_float(r["completion_time_s"]) or 0 for r in groups[rate]]
        m = sum(vals) / len(vals)
        means_total.append(m)
        lows_total.append(m - min(vals))
        highs_total.append(max(vals) - m)
        means_actual.append(max(0.0, m - QUIESCENCE_GRACE_S))
        max_gens.append(max(int(float(r["max_generation_observed"]))
                            for r in groups[rate]))

    fig, ax = plt.subplots(figsize=(6, 3.8))
    ax.errorbar(xs, means_total, yerr=[lows_total, highs_total],
                fmt="o-", color="#1f77b4", capsize=3,
                label="total wallclock (mean, min–max)")
    ax.plot(xs, means_actual, "s--", color="#ff7f0e",
            label=f"actual completion time (− {QUIESCENCE_GRACE_S:.0f} s grace)")
    ax.axhline(QUIESCENCE_GRACE_S, color="#888", linestyle=":", linewidth=1,
               label=f"{QUIESCENCE_GRACE_S:.0f} s quiescence grace floor")
    ax.set_xlabel("Abort rate R (%)")
    ax.set_ylabel("Completion time (s)")
    ax.set_title("Completion time vs. abort rate (N=4 writers)")
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(loc="upper left", fontsize=8)

    for x, yt, mg in zip(xs, means_total, max_gens):
        ax.annotate(f"max_gen={mg}", (x, yt),
                    textcoords="offset points", xytext=(6, 6),
                    fontsize=8, color="#444")

    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


# -----------------------------------------------------------------------------
# Plot 3: max generation observed across both varies
# -----------------------------------------------------------------------------


def plot_max_generation(rows: list[dict], out: Path) -> None:
    if not rows:
        return
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.5))
    # Left: vary writers
    wrows = [r for r in rows if r["vary"] == "writers"]
    wrows.sort(key=lambda r: int(r["n_writers"]))
    if wrows:
        xs = [int(r["n_writers"]) for r in wrows]
        mg = [int(float(r["max_generation_observed"])) for r in wrows]
        axes[0].bar([str(x) for x in xs], mg, color="#1f77b4")
        axes[0].set_xlabel("# writers (N)")
        axes[0].set_ylabel("max_generation observed")
        axes[0].set_title("Writers (vary N) (R=0%)")
        axes[0].grid(True, axis="y", linestyle=":", alpha=0.5)

    # Right: vary abort
    arows = [r for r in rows if r["vary"] == "abort"]
    arows.sort(key=lambda r: float(r["abort_rate"]))
    if arows:
        xs = [100 * float(r["abort_rate"]) for r in arows]
        mg = [int(float(r["max_generation_observed"])) for r in arows]
        axes[1].bar([f"{x:.0f}%" for x in xs], mg, color="#ff7f0e")
        axes[1].set_xlabel("Abort rate R (%)")
        axes[1].set_ylabel("max_generation observed")
        axes[1].set_title("Abort (vary R) (N=4)")
        axes[1].grid(True, axis="y", linestyle=":", alpha=0.5)

    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


# -----------------------------------------------------------------------------
# Plot 4: generation distribution per trial
# -----------------------------------------------------------------------------


def plot_generation_distribution(rows: list[dict], out: Path) -> None:
    """Stacked horizontal bars: one row per abort rate (mean across repeats),
    one segment per final generation. Generation 0 is a task that completed
    without a retry."""
    if not rows:
        return
    abort_rows = [r for r in rows if r["vary"] == "abort"]
    if not abort_rows:
        return

    # Sum the generation distributions per abort rate.
    from collections import defaultdict
    groups: dict[float, list[dict]] = defaultdict(list)
    for r in abort_rows:
        groups[float(r["abort_rate"])].append(r)

    rates = sorted(groups.keys())
    all_gens: set[int] = set()
    per_rate_mean: dict[float, dict[int, float]] = {}
    for rate in rates:
        rs = groups[rate]
        summed: dict[int, int] = {}
        for r in rs:
            for g, c in parse_gen_dist(r["generation_distribution"]).items():
                summed[g] = summed.get(g, 0) + c
        n = len(rs)
        per_rate_mean[rate] = {g: c / n for g, c in summed.items()}
        all_gens.update(summed.keys())
    gens = sorted(all_gens)

    # Paper figure fonts; wide and short.
    LABEL_SIZE, TICK_SIZE, LEGEND_SIZE = 20, 18, 17
    fig, ax = plt.subplots(figsize=(9.2, 0.9 + 0.42 * len(rates)))
    ys = list(range(len(rates)))
    # Normalize each row to 0..1.
    totals = [sum(per_rate_mean[rate].values()) or 1.0 for rate in rates]
    lefts = [0.0] * len(rates)
    cmap = plt.get_cmap("viridis")
    for i, g in enumerate(gens):
        widths = [per_rate_mean[rate].get(g, 0.0) / tot
                  for rate, tot in zip(rates, totals)]
        ax.barh(ys, widths, left=lefts,
                label=f"v{g}",
                color=cmap(i / max(1, len(gens) - 1)),
                edgecolor="white", linewidth=0.5)
        lefts = [l + w for l, w in zip(lefts, widths)]
    ax.set_yticks(ys)
    ax.set_yticklabels([f"R={int(100 * r)}%" for r in rates], fontsize=TICK_SIZE)
    ax.tick_params(axis="x", labelsize=TICK_SIZE)
    # Tight y limits remove the automatic top and bottom margin and put the
    # first row at the top.
    ax.set_ylim(len(rates) - 0.5, -0.5)
    ax.set_xlim(0, 1)
    ax.set_xlabel("Fraction of tasks", fontsize=LABEL_SIZE)
    # No title: the paper caption covers it. Two legend columns keep the
    # legend short. The upper-left anchor starts the entries level with the
    # top bar. matplotlib puts a legend title above the entries, so the label
    # "Version number" is placed below the legend once its extent is known.
    leg = ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.08),
                    fontsize=LEGEND_SIZE, frameon=False,
                    ncol=2, columnspacing=1.0, handletextpad=0.4)
    ax.grid(True, axis="x", linestyle=":", alpha=0.5)
    fig.tight_layout()
    fig.canvas.draw()  # the legend extent needs a layout pass
    bbox_fig = leg.get_window_extent(fig.canvas.get_renderer()).transformed(
        fig.transFigure.inverted())
    fig.text((bbox_fig.x0 + bbox_fig.x1) / 2, bbox_fig.y0 - 0.02,
             "Version number", ha="center", va="top", fontsize=LEGEND_SIZE)
    # bbox_inches keeps the legend and its label, which sit outside the axes.
    fig.savefig(out, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("trials_csv", type=Path)
    args = p.parse_args()
    if not args.trials_csv.exists():
        print(f"missing: {args.trials_csv}", file=sys.stderr)
        return 1
    results_dir = args.trials_csv.parent
    plots_dir = results_dir / "plots"
    plots_dir.mkdir(exist_ok=True)
    rows = load_trials(args.trials_csv)
    plot_completion_vs_writers(rows, plots_dir / "completion_time_vs_writers.pdf")
    plot_completion_vs_abort(rows, plots_dir / "completion_time_vs_abort.pdf")
    plot_max_generation(rows, plots_dir / "max_generation.pdf")
    plot_generation_distribution(rows, plots_dir / "generation_dist.pdf")
    print(f"plots -> {plots_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
