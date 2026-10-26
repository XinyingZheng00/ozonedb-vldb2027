#!/usr/bin/env python3
"""Writer activity timeline of one churn trial.

Parses the writer_*.log files of a trial dir for paired "Compaction started"
and "Compaction completed" timestamps, and draws one bar per compaction and
writer over wall-clock time. The plot shows when a writer is busy or idle,
and which writer holds the tail of the cascade.

Usage:
    python3 plot_writer_activity.py <results_dir>/per_trial/<trial_id>
    # writes <results_dir>/plots/writer_activity_<trial_id>.pdf
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}:\d{3}) - Compaction (started|completed):")


def parse_ts(s: str) -> float:
    # Format: "2026-01-01 12:34:56:789" -> unix time in seconds.
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S:%f")
    return dt.timestamp()


def parse_log(path: Path) -> list[tuple[float, float]]:
    """Return list of (start_unix_s, end_unix_s) intervals."""
    intervals: list[tuple[float, float]] = []
    pending_start: float | None = None
    with path.open() as f:
        for line in f:
            m = TS_RE.match(line)
            if not m:
                continue
            ts = parse_ts(m.group(1))
            kind = m.group(2)
            if kind == "started":
                pending_start = ts
            elif kind == "completed" and pending_start is not None:
                intervals.append((pending_start, ts))
                pending_start = None
    return intervals


def plot_trial(trial_dir: Path, out: Path, ymax: float | None = None,
               show_ylabel: bool = True) -> None:
    # Numeric sort: writer_2 before writer_10.
    writer_logs = sorted(trial_dir.glob("writer_*.log"),
                         key=lambda p: int(re.search(r"writer_(\d+)\.log", p.name).group(1)))
    if not writer_logs:
        print(f"No writer_*.log under {trial_dir}", file=sys.stderr)
        return
    series: list[tuple[str, list[tuple[float, float]]]] = []
    t0: float | None = None
    for p in writer_logs:
        ivs = parse_log(p)
        if ivs and (t0 is None or ivs[0][0] < t0):
            t0 = ivs[0][0]
        # writer index from filename: writer_<i>.log
        idx = re.search(r"writer_(\d+)\.log", p.name).group(1)
        series.append((f"W{idx}", ivs))
    if t0 is None:
        print(f"No compaction intervals parsed under {trial_dir}", file=sys.stderr)
        return

    # Trial end: the last completion across writers.
    t_end = max((iv[1] for _, ivs in series for iv in ivs), default=t0)
    duration = t_end - t0

    n_writers = len(series)
    # Writers on the x axis, time on the y axis: a tall narrow panel, so two
    # panels fit side by side. The width grows with the writer count; the
    # height is fixed so panels share a time scale.
    FIG_H = 3
    fig_w = max(2.6, 1.0 + 0.32 * n_writers)
    fig, ax = plt.subplots(figsize=(fig_w, FIG_H))
    bar_color = "#1f77b4"
    for i, (label, ivs) in enumerate(series):
        for s, e in ivs:
            ax.bar(i, e - s, bottom=s - t0, width=0.8, align="center",
                   facecolor=bar_color, edgecolor="white", linewidth=0.3)
    ax.set_xticks(range(n_writers))
    ax.set_xticklabels([str(i) for i in range(n_writers)],
                       fontsize=19 if n_writers <= 8 else 15)
    ax.tick_params(axis="y", labelsize=19)
    ax.set_xlim(-0.6, n_writers - 0.4)
    # t=0 at the bottom. A caller-supplied ymax gives several panels one time scale.
    ax.set_ylim(0, ymax if ymax is not None else max(duration, 1.0))
    ax.set_xlabel("Writer", fontsize=19)
    # Two lines: the label is tall on a narrow panel.
    ax.set_ylabel("Time since first\ncompaction (s)" if show_ylabel else "",
                  fontsize=19)
    ax.grid(True, axis="y", linestyle=":", alpha=0.5)
    # Fixed absolute margins (inches) on every panel, and no bbox_inches="tight":
    # that crop follows each panel's own content, so side-by-side panels
    # would not line up.
    left_in = 1.05 if show_ylabel else 0.52  # 2-line rotated label + y-tick nums
    fig.subplots_adjust(left=left_in / fig_w, right=1 - 0.12 / fig_w,
                        bottom=0.72 / FIG_H, top=1 - 0.12 / FIG_H)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    total_busy = sum(e - s for _, ivs in series for s, e in ivs)
    print(f"  {trial_dir.name}: {n_writers} writers, "
          f"{sum(len(ivs) for _, ivs in series)} compactions, "
          f"{duration:.1f}s wall, "
          f"avg busy = {total_busy / max(1, n_writers):.1f}s "
          f"(util = {100 * total_busy / max(0.001, n_writers * duration):.1f}%)")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("trial_dir", type=Path,
                   help="Path to per_trial/<trial_id> directory, OR a "
                        "results/v4_<ts> dir (will plot all trials inside).")
    p.add_argument("--ymax", type=float, default=None,
                   help="Fixed upper y-limit in seconds, so panels plotted "
                        "in separate runs (e.g. N=8 and N=16) share a time scale.")
    p.add_argument("--no-ylabel", action="store_true",
                   help="Omit the y-axis label (for the right panel of a "
                        "side-by-side pair that already has it on the left).")
    args = p.parse_args()

    if not args.trial_dir.exists():
        print(f"missing: {args.trial_dir}", file=sys.stderr)
        return 1

    # A results root: plot every per_trial subdir.
    per_trial_root = args.trial_dir / "per_trial"
    if per_trial_root.is_dir():
        results_dir = args.trial_dir
        trial_dirs = sorted(d for d in per_trial_root.iterdir() if d.is_dir())
    else:
        # Single trial dir.
        trial_dirs = [args.trial_dir]
        # Walk up to results_dir
        if args.trial_dir.parent.name == "per_trial":
            results_dir = args.trial_dir.parent.parent
        else:
            results_dir = args.trial_dir.parent

    plots_dir = results_dir / "plots"
    for d in trial_dirs:
        out = plots_dir / f"writer_activity_{d.name}.pdf"
        plot_trial(d, out, ymax=args.ymax, show_ylabel=not args.no_ylabel)
    print(f"plots -> {plots_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
