#!/usr/bin/env python3
"""Architecture diagram for Figure 18, drawn with Graphviz in the same style
as figure17_serval/scripts/plot_architecture.py. Not in the paper.

It shows only the `metrics` history write, the one write that differs
between the three backends. Runs, experiments and `latest_metrics` stay in
SQL for every backend, including ozonedb (see ozonedb_metric_store.py), so
they're left out.

Each column: P Ray Tune trial processes, 3 threads each (training loss,
cpu/mem monitor, disk monitor, each logging its own keys), writing to one
store.

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
    """The box of P Ray Tune trial processes, 3 threads each; every thread
    logs its own metric keys."""
    c.node(f"{prefix}_workers",
           node_label("P Ray Tune trial processes",
                       "3 threads each: loss / cpu+mem / disk I/O"))


def direct_converge(g, prefix, accent, subtitle, title, db_label, tag):
    """One column: the worker box writes to one store. For postgres the
    edge is a network call to the server (directed arrow); for sqlite and
    ozonedb it's direct file access (undirected, as in Figure 17)."""
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
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "plots" / "architecture"))
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
