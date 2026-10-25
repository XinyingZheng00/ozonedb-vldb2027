#!/usr/bin/env python3
"""Paper-style architecture diagram via Graphviz (auto-layout). It uses
the same tool and visual language as serval-audit/plots/plot_architecture.py
(Phase 1).

The diagram is scoped to the `metrics` history write only. That is the one
operation that actually differs across the three backends, and the one
this whole benchmark measures. `latest_metrics`, runs and experiments are
real for all three arms too (including the `ozonedb` arm, see
ozonedb_metric_store.py). But they are identical infrastructure across
all three columns, not part of the comparison. So the diagram deliberately
leaves them out, rather than draw them and then explain them away. An
earlier version of this diagram drew `ozonedb` as a two-edge hybrid
(bridged `metrics` plus unbridged SQL for the rest). The goal was to be
literally complete about the shape of the bridge. That is still true, and
the own docstring of ozonedb_metric_store.py still documents it. But it
is not the job of this diagram to show it. The scope of the diagram is
"the thing being compared", not "everything the bridge touches".

With that scope, all three columns reduce to the same simple shape as in
Phase 1. P Ray Tune trial processes converge on one store for the
`metrics` write. The processes are real OS processes with 3 threads each:
a main training thread plus 2 independent monitor threads. Each thread
writes its own key.

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
    """The single P-symmetric-processes box shared by all three columns:
    real Ray Tune trials, each with 3 real threads inside. Each thread
    writes its own distinct metric key. No two threads ever share a key.
    This is why 3 threads do not reopen the latest_metrics race (see
    Phase 2b/2c)."""
    c.node(f"{prefix}_workers",
           node_label("P Ray Tune trial processes",
                       "3 threads each: loss / cpu+mem / disk I/O"))


def direct_converge(g, prefix, accent, subtitle, title, db_label, tag):
    """All three columns: the worker box converges directly on one store
    for the `metrics` history write. For postgres this edge is a real
    network call (client-server). It is drawn as a directed arrow with a
    label that says so. For sqlite and ozonedb it is direct file access.
    It is drawn undirected, peer-style, which matches the convention of
    Phase 1 for "opens the store directly"."""
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
