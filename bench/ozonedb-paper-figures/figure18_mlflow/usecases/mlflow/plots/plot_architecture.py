#!/usr/bin/env python3
"""Paper-style architecture diagram via Graphviz (auto-layout), same tool
and visual language as serval-audit/plots/plot_architecture.py (Phase 1).

Scoped to the `metrics` history write only -- the one operation that
actually differs across the three backends and the one this whole
benchmark measures. `latest_metrics`/runs/experiments are real for all
three arms too (including `ozonedb`'s -- see ozonedb_metric_store.py),
but they're identical infrastructure across all three columns, not part
of the comparison, so they're deliberately left out rather than drawn and
then explained away. An earlier version of this diagram drew `ozonedb` as
a two-edge hybrid (bridged `metrics` + unbridged SQL for the rest) to be
literally complete about the bridge's shape; that's still true and still
documented in ozonedb_metric_store.py's own docstring, just not
this diagram's job to show once the diagram's scope is "the thing being
compared," not "everything the bridge touches."

With that scoping, all three columns reduce to the same simple shape as
Phase 1's: P Ray Tune trial processes (real OS processes, 3 threads each
-- main training thread + 2 independent monitor threads, each writing its
own key) converging on one store for the `metrics` write.

Usage:
    python3 plot_architecture.py [--out PATH]
"""
import argparse
from pathlib import Path

import graphviz

ACCENT = {"sqlite": "#eb6834", "ozonedb": "#2a78d6", "postgres": "#1baf7a"}
ICON = {"conflict": "#d03b3b", "ok": "#0ca30c"}
GLYPH = {"conflict": "!", "ok": "&#10003;"}


def node_label(title, sub=None):
    if sub:
        return (f'<<font point-size="12"><b>{title}</b></font><br/>'
                f'<font point-size="10"><i>{sub}</i></font>>')
    return f'<<font point-size="12"><b>{title}</b></font>>'


def cluster_title(title, subtitle):
    return (f'<<font point-size="15"><b>{title}</b></font><br/>'
            f'<font point-size="10" color="#6b6a65"><i>{subtitle}</i></font>>')


def status_node(name, kind):
    return dict(name=name, label=f"<{GLYPH[kind]}>", shape="circle", style="filled",
                fillcolor=ICON[kind], fontcolor="white", fontsize="11", width="0.32",
                height="0.32", fixedsize="true", penwidth="0")


def workers_node(prefix, c):
    """The single P-symmetric-processes box shared by all three columns --
    real Ray Tune trials, each with 3 real threads inside, each thread
    writing its own distinct metric key (no two ever share a key, which
    is why 3 threads don't reopen the latest_metrics race -- see Phase
    2b/2c)."""
    c.node(f"{prefix}_workers",
           node_label("P Ray Tune trial processes",
                       "3 threads each: loss / cpu+mem / disk I/O"))


def direct_converge(g, prefix, accent, subtitle, title, db_label, tag):
    """All three columns: the worker box converges directly on one store
    for the `metrics` history write. For postgres this edge is a real
    network call (client-server), drawn as a directed arrow labeled
    accordingly; for sqlite/ozonedb it's direct file access (undirected,
    peer-style, matching Phase 1's convention for "opens the store
    directly")."""
    with g.subgraph(name=f"cluster_{prefix}") as c:
        c.attr(label=cluster_title(title, subtitle), labelloc="t", fontname="Helvetica",
               color=accent, penwidth="1.4", style="rounded", margin="18")
        workers_node(prefix, c)
        s = status_node(f"{prefix}_icon", tag)
        c.node(s["name"], s["label"], shape=s["shape"], style=s["style"],
               fillcolor=s["fillcolor"], fontcolor=s["fontcolor"], fontsize=s["fontsize"],
               width=s["width"], height=s["height"], fixedsize=s["fixedsize"], penwidth=s["penwidth"])
        c.node(f"{prefix}_db", node_label(*db_label))
        if prefix == "postgres":
            c.edge(f"{prefix}_workers", s["name"], arrowhead="normal", arrowsize="0.6",
                   label="network", fontsize="8", fontcolor="#6b6a65")
        else:
            c.edge(f"{prefix}_workers", s["name"], arrowhead="none")
        c.edge(s["name"], f"{prefix}_db", arrowhead="none")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "architecture"))
    args = ap.parse_args()

    g = graphviz.Digraph("architecture", format="pdf")
    g.attr(rankdir="TB", splines="line", nodesep="0.4", ranksep="0.32", compound="true",
           fontname="Helvetica")
    g.attr("node", shape="box", style="rounded,filled", fillcolor="#f5f4f1",
           color="#3a3a38", fontname="Helvetica", margin="0.12,0.09")
    g.attr("edge", color="#8a8880", fontname="Helvetica")

    direct_converge(g, "sqlite", ACCENT["sqlite"], "real MLflow default, direct file access",
                     "SQLite", ("SQLite file", "metrics (history)"), "conflict")

    direct_converge(g, "ozonedb", ACCENT["ozonedb"], "metrics history bridged",
                     "OzoneDB", ("OzoneDB store", "metrics (history)"), "ok")

    direct_converge(g, "postgres", ACCENT["postgres"], "client-server, real network hop",
                     "Postgres", ("Postgres database", "metrics (history)"), "ok")

    g.attr(label=(
        '<<table border="0" cellborder="0" cellspacing="6">'
        '<tr>'
        f'<td><font color="{ICON["conflict"]}" point-size="14"><b>&#9679;</b></font>'
        '<font point-size="10"> real conflict (database is locked)</font></td>'
        f'<td><font color="{ICON["ok"]}" point-size="14"><b>&#9679;</b></font>'
        '<font point-size="10"> no conflict observed (P=1-48)</font></td>'
        '</tr></table>>'
    ), labelloc="b", fontname="Helvetica")

    out_path = g.render(args.out, cleanup=True)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
