"""Shared loading/styling helpers for the persistent-adder 3-way comparison
plots. Each plot_*.py script imports this module and produces exactly one
PDF. So you can regenerate each figure on its own without touching the others.

Colors match the categorical palette that the earlier HTML charts of this
investigation used. The palette is validated all-pairs colorblind-safe for a
3-series set:
  OzoneDB      #2a78d6  (blue)
  SQLite       #eb6834  (orange)
  sqlite_rest  #1baf7a  (aqua/green)
"""
import csv
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CSV = HERE.parent / "persistent_adder_results" / "trials.csv"

BACKEND_LABEL = {
    "ozonedb": "OzoneDB",
    "sqlite": "SQLite",
    "sqlite_rest": "Re-architected",
}
BACKEND_COLOR = {
    "ozonedb": "#2a78d6",
    "sqlite": "#eb6834",
    "sqlite_rest": "#1baf7a",
}
BACKEND_ORDER = ["ozonedb", "sqlite", "sqlite_rest"]


def load_rows(csv_path=None):
    csv_path = Path(csv_path) if csv_path else DEFAULT_CSV
    if not csv_path.exists():
        raise FileNotFoundError(f"{csv_path} not found -- run run_persistent_adder_benchmark.py first")
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def all_ns(rows):
    return sorted({int(r["n_workers"]) for r in rows})


def series_with_error(rows, backend, field, ns_all):
    """Return (x_positions, means, stds) sorted by N, for one backend/field.
    The function aggregates across repeats. Repeats are multiple rows with
    the same backend/N. The 'repeat' column tells them apart when
    --repeats > 1. std is 0.0 for an N with only a single repeat. It is
    never silently dropped. So a lone point still renders, with an invisible
    error bar, instead of vanishing. Rows where the field is missing or
    empty are skipped (for example daemon_* for sqlite_rest). x_positions
    are ORDINAL (rank in ns_all), not the raw N value. N doubles from 1 to 8,
    then steps by 4 up to 32. So on a linear axis, the low end collapses
    into an unreadable cluster relative to the 1-32 range. The tick labels
    still show the actual N values."""
    by_n = defaultdict(list)
    for r in rows:
        if r["backend"] != backend:
            continue
        v = r.get(field)
        if v is None or v == "":
            continue
        by_n[int(r["n_workers"])].append(float(v))
    ns = sorted(by_n)
    if not ns:
        return [], [], []
    xs = [ns_all.index(n) for n in ns]
    means = [statistics.mean(by_n[n]) for n in ns]
    stds = [statistics.stdev(by_n[n]) if len(by_n[n]) > 1 else 0.0 for n in ns]
    return xs, means, stds


def style_axes(ax, ns, ylabel, title, logy=True):
    # The label is short on purpose. It must fit under a compact paper figure.
    # The N+1-actors nuance (daemon adder for sqlite/ozonedb, extra REST
    # client for sqlite_rest) belongs in the caption/analysis text. Do not
    # cram it into the axis label.
    ax.set_xlabel("N (concurrent CLI workers)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xticks(range(len(ns)))
    ax.set_xticklabels([str(n) for n in ns])
    if logy:
        ax.set_yscale("log")
    ax.grid(True, which="both", axis="y", linewidth=0.4, alpha=0.5)
    ax.grid(True, which="major", axis="x", linewidth=0.4, alpha=0.3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
