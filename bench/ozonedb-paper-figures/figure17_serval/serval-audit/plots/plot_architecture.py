#!/usr/bin/env python3
"""Paper-style architecture diagram via Graphviz (not matplotlib). The auto
layout means no hand-computed box/text coordinates. Those coordinates kept
causing overflow/spacing bugs in an earlier matplotlib version.

All three columns share the same two-boxes-on-top visual shape. But the
diagram draws two distinct relationships, because they ARE architecturally
different:
  - SQLite / OzoneDB: the daemon and the N CLI workers are independent
    peers. Each opens the store directly. Two boxes converge on one DB box
    with undirected lines. The store is what they share, not what either
    of them calls.
  - Server-based solution: the N CLI workers and the daemon-like client
    are not peers of each other or of the store. Both call INTO the
    RESTful API-based server (directed arrows). Only that server ever
    touches the file below it.

Usage:
    python3 plot_architecture.py [--out PATH]
"""
import argparse
from pathlib import Path

import graphviz

ACCENT = {"sqlite": "#eb6834", "ozonedb": "#2a78d6", "rest": "#1baf7a"}
ICON = {"conflict": "#d03b3b", "retry": "#b8860b", "ok": "#0ca30c"}
GLYPH = {"conflict": "!", "retry": "&#8635;", "ok": "&#10003;"}


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


def peers_converge(g, prefix, accent, subtitle, title, top_a, top_b, db_label, tag):
    """SQLite/OzoneDB shape: two independent peer boxes. Each opens the
    store directly. They converge on one DB box."""
    with g.subgraph(name=f"cluster_{prefix}") as c:
        c.attr(label=cluster_title(title, subtitle), labelloc="t", fontname="Helvetica",
               color=accent, penwidth="1.4", style="rounded", margin="18")
        c.node(f"{prefix}_a", node_label(*top_a))
        c.node(f"{prefix}_b", node_label(*top_b))
        s = status_node(f"{prefix}_icon", tag)
        c.node(s["name"], s["label"], shape=s["shape"], style=s["style"],
               fillcolor=s["fillcolor"], fontcolor=s["fontcolor"], fontsize=s["fontsize"],
               width=s["width"], height=s["height"], fixedsize=s["fixedsize"], penwidth=s["penwidth"])
        c.node(f"{prefix}_db", node_label(db_label))
        c.edge(f"{prefix}_a", s["name"], arrowhead="none")
        c.edge(f"{prefix}_b", s["name"], arrowhead="none")
        c.edge(s["name"], f"{prefix}_db", arrowhead="none")
        c.body.append(f'{{rank=same; {prefix}_a; {prefix}_b}}')


def linear_chain(g, prefix, accent, subtitle, title, top_a, top_b, server, db_label, tag):
    """Re-architected shape: the same two-box top row as the other columns,
    for visual consistency. But the relationship is different. Both boxes
    call INTO the server (directed arrows). They are not independent peers
    that open the store directly. Only the server ever touches the file."""
    with g.subgraph(name=f"cluster_{prefix}") as c:
        c.attr(label=cluster_title(title, subtitle), labelloc="t", fontname="Helvetica",
               color=accent, penwidth="1.4", style="rounded", margin="18")
        c.node(f"{prefix}_a", node_label(*top_a))
        c.node(f"{prefix}_b", node_label(*top_b))
        c.node(f"{prefix}_server", node_label(*server))
        s = status_node(f"{prefix}_icon", tag)
        c.node(s["name"], s["label"], shape=s["shape"], style=s["style"],
               fillcolor=s["fillcolor"], fontcolor=s["fontcolor"], fontsize=s["fontsize"],
               width=s["width"], height=s["height"], fixedsize=s["fixedsize"], penwidth=s["penwidth"])
        c.node(f"{prefix}_db", node_label(db_label))
        c.edge(f"{prefix}_a", f"{prefix}_server", arrowhead="normal", arrowsize="0.6")
        c.edge(f"{prefix}_b", f"{prefix}_server", arrowhead="normal", arrowsize="0.6")
        c.edge(f"{prefix}_server", s["name"], arrowhead="none")
        c.edge(s["name"], f"{prefix}_db", arrowhead="none")
        c.body.append(f'{{rank=same; {prefix}_a; {prefix}_b}}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "architecture"))
    args = ap.parse_args()

    g = graphviz.Digraph("architecture", format="pdf")
    g.attr(rankdir="TB", splines="line", nodesep="0.35", ranksep="0.3", compound="true",
           fontname="Helvetica")
    g.attr("node", shape="box", style="rounded,filled", fillcolor="#f5f4f1",
           color="#3a3a38", fontname="Helvetica", margin="0.12,0.09")
    g.attr("edge", color="#8a8880", fontname="Helvetica")

    peers_converge(g, "sqlite", ACCENT["sqlite"], "direct access, retry-patched",
                    "SQLite (sleep-retry)",
                    ("Daemon", "persistent conn."), ("N CLI workers", "fresh proc/op"),
                    "SQLite file", "retry")

    peers_converge(g, "ozonedb", ACCENT["ozonedb"], "same daemon+CLI shape", "OzoneDB",
                    ("Daemon", "persistent conn."), ("N CLI workers", "fresh proc/op"),
                    "OzoneDB store", "ok")

    linear_chain(g, "rest", ACCENT["rest"], "single daemon, REST", "Server-based solution",
                 ("N CLI workers", "REST calls"), ("1 daemon-like client", "REST calls"),
                 ("RESTful API-based server", "sole owner of file"),
                 "SQLite file", "ok")

    g.attr(label=(
        '<<table border="0" cellborder="0" cellspacing="6">'
        '<tr>'
        f'<td><font color="{ICON["conflict"]}" point-size="14"><b>&#9679;</b></font>'
        '<font point-size="10"> real conflict</font></td>'
        f'<td><font color="{ICON["retry"]}" point-size="14"><b>&#9679;</b></font>'
        '<font point-size="10"> retried, not free</font></td>'
        f'<td><font color="{ICON["ok"]}" point-size="14"><b>&#9679;</b></font>'
        '<font point-size="10"> no conflict possible</font></td>'
        '</tr></table>>'
    ), labelloc="b", fontname="Helvetica")

    out_path = g.render(args.out, cleanup=True)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
