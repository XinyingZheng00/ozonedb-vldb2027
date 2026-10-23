"""Shared loading/styling helpers for the persistent-adder 3-way comparison
plots. Each plot_*.py script imports this and produces exactly one PDF, so
figures can be regenerated individually without touching the others.

Colors match the categorical palette used throughout this investigation's
earlier HTML charts (validated all-pairs colorblind-safe for a 3-series set):
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
    """Returns (x_positions, means, stds) sorted by N, for one backend/field,
    aggregating across repeats (multiple rows sharing the same backend/N,
    distinguished by the 'repeat' column when --repeats > 1). std is 0.0
    for an N with only a single repeat -- never silently dropped, so a lone
    point still renders (with an invisible error bar) rather than vanishing.
    Skips rows where the field is missing/empty (e.g. daemon_* for
    sqlite_rest). x_positions are ORDINAL (rank in ns_all), not the raw N
    value -- N=1..8 doubles, then steps by 4 up to 32, so on a linear axis
    the low end collapses into an unreadable cluster relative to the 1-32
    range; the actual N values are still shown as tick labels."""
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
    # Kept short deliberately -- it must fit under a compact paper figure.
    # The N+1-actors nuance (daemon adder for sqlite/ozonedb, extra REST
    # client for sqlite_rest) belongs in the caption/analysis text, not
    # crammed into the axis label.
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
