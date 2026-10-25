"""Shared load and style helpers for the 3-way comparison plots of the
MLflow P-sweep (Phase 2c, ray_mlflow_swapin_3thread_5s_psweep/trials.csv).
It mirrors the shape of serval-audit/plots/plot_common.py (the same
field-adaptation pattern that kv_writer/orchestrator.py already uses
across phases). It is adapted to the actual CSV columns of this phase:
n_processes, not n_workers; no daemon_* side channel; and
error_rate_metrics_only next to the raw error_rate.

The colors reuse the same categorical palette as serval-audit/plots
(Phase 1), for cross-figure consistency in the paper. OzoneDB and SQLite
keep their established colors. Postgres takes the green of the third
slot. In Phase 1, sqlite_rest had that role (the client-server,
re-architected fix):
  OzoneDB   #2a78d6  (blue)
  SQLite    #eb6834  (orange)
  Postgres  #1baf7a  (aqua/green)
"""
import csv
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CSV = (HERE.parent.parent.parent / "data"
               / "ray_mlflow_swapin_3thread_5s_psweep" / "trials.csv")

BACKEND_LABEL = {
    "ozonedb": "OzoneDB (metrics bridged, latest_metrics on SQL)",
    "sqlite": "SQLite (real MLflow default)",
    "postgres": "Postgres (client-server)",
    "postgres_serialized": "Postgres (commits serialized, batch=1 control)",
}
BACKEND_COLOR = {
    "ozonedb": "#2a78d6",
    "sqlite": "#eb6834",
    "postgres": "#1baf7a",
    "postgres_serialized": "#1baf7a",  # same hue as postgres. It is a control variant, not a 4th system
}
# The serialized control is dashed. So it reads as "postgres, with batching
# artificially disabled", not as a fourth independent competitor.
BACKEND_LINESTYLE = {
    "ozonedb": "-",
    "sqlite": "-",
    "postgres": "-",
    "postgres_serialized": "--",
}
BACKEND_ORDER = ["ozonedb", "sqlite", "postgres", "postgres_serialized"]


def load_rows(csv_path=None):
    csv_path = Path(csv_path) if csv_path else DEFAULT_CSV
    if not csv_path.exists():
        raise FileNotFoundError(f"{csv_path} not found -- run run_ray_swapin.py first")
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def all_ps(rows):
    return sorted({int(r["n_processes"]) for r in rows})


def series_with_error(rows, backend, field, ps_all):
    """Returns (x_positions, means, stds) sorted by P, for one backend and
    field, aggregated across repeats. The std is 0.0 for a P with only one
    repeat. It is never silently dropped. So a lone point still renders
    (with an invisible error bar) and does not vanish. The x_positions are
    ORDINAL (the rank in ps_all), not the raw P value. P=1..8 doubles, then
    steps by 4 up to 32, then jumps to 48. So a linear axis would collapse
    the low end into an unreadable cluster. The actual P values are still
    shown as tick labels."""
    by_p = defaultdict(list)
    for r in rows:
        if r["backend"] != backend:
            continue
        v = r.get(field)
        if v is None or v == "":
            continue
        by_p[int(r["n_processes"])].append(float(v))
    ps = sorted(by_p)
    if not ps:
        return [], [], []
    xs = [ps_all.index(p) for p in ps]
    means = [statistics.mean(by_p[p]) for p in ps]
    stds = [statistics.stdev(by_p[p]) if len(by_p[p]) > 1 else 0.0 for p in ps]
    return xs, means, stds


def style_axes(ax, ps, ylabel, title, logy=True):
    ax.set_xlabel("P (concurrent Ray Tune trial processes, 3 threads each)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xticks(range(len(ps)))
    ax.set_xticklabels([str(p) for p in ps])
    if logy:
        ax.set_yscale("log")
    ax.grid(True, which="both", axis="y", linewidth=0.4, alpha=0.5)
    ax.grid(True, which="major", axis="x", linewidth=0.4, alpha=0.3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
