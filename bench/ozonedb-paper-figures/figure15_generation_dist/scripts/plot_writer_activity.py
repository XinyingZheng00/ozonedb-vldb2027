#!/usr/bin/env python3
"""Gantt-style writer-activity timeline.

For a given trial dir, this script parses the per-writer logs (writer_*.log).
It pairs the "Compaction started" and "Compaction completed" timestamps. Then
it renders a horizontal-bar plot that shows the busy intervals of each writer
over wall-clock time. The plot shows when the writers are saturated and when
they are idle. It also shows which writer holds the cascade tail at the end.

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
    # The input format is 2026-05-01 03:43:12:626. The output is unix-time seconds.
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
    # Sort numerically, so writer_2 comes before writer_10. A plain sorted() is lexical.
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
        # Take the writer index from the filename writer_<i>.log.
        idx = re.search(r"writer_(\d+)\.log", p.name).group(1)
        series.append((f"W{idx}", ivs))
    if t0 is None:
        print(f"No compaction intervals parsed under {trial_dir}", file=sys.stderr)
        return

    # The trial wallclock end is the latest completion across all writers.
    t_end = max((iv[1] for _, ivs in series for iv in ivs), default=t0)
    duration = t_end - t0

    n_writers = len(series)
    # Transposed layout: the writers are on the x-axis, and wall-clock time
    # runs DOWN the y-axis. This makes each panel tall and narrow, so the N=8
    # and N=16 panels sit side by side in the paper. The width grows with the
    # writer count. The height is fixed, so the two panels share a time scale
    # visually.
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
    # t=0 is at the bottom, and time flows upward. If the caller supplies ymax,
    # the N=8 and N=16 panels share an identical time scale.
    ax.set_ylim(0, ymax if ymax is not None else max(duration, 1.0))
    ax.set_xlabel("Writer", fontsize=19)
    # Use two lines. At this font size, the label is tall on the narrow N=8 panel.
    ax.set_ylabel("Time since first\ncompaction (s)" if show_ylabel else "",
                  fontsize=19)
    ax.grid(True, axis="y", linestyle=":", alpha=0.5)
    # Pin the axes rectangle to identical ABSOLUTE margins (inches) on every
    # panel. Do NOT crop with bbox_inches="tight". That crop hugs the content
    # of each panel. The N=8 panel has a 2-line y-label and big x-ticks. The
    # N=16 panel has no y-label and smaller x-ticks. With the crop, their plot
    # boxes end at different distances from the bottom edge of the PDF. Then
    # the bottoms do not line up side by side. Fixed margins and equal-ratio
    # \includegraphics widths keep them aligned.
    left_in = 1.05 if show_ylabel else 0.52  # 2-line rotated label and y-tick numbers
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

    # If the user passed a results root, find all per_trial subdirs.
    per_trial_root = args.trial_dir / "per_trial"
    if per_trial_root.is_dir():
        results_dir = args.trial_dir
        trial_dirs = sorted(d for d in per_trial_root.iterdir() if d.is_dir())
    else:
        # Single trial dir.
        trial_dirs = [args.trial_dir]
        # Walk up to results_dir.
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
