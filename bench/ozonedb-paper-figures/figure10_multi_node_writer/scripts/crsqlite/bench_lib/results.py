"""YCSB result parsing, aggregation, and file naming.

The parsing and the write_aggregate arithmetic are ported verbatim from
ozonedb/bench/scripts/local/load_local_ycsb_multiproc.py. Identical math is
what makes the aggregate files of the two engines comparable:

  [AGGREGATE] Throughput(ops/sec)          total ops / parent wall clock
                                           (straggler-inclusive)
  [AGGREGATE] SumWriterThroughput(ops/sec) sum of per-writer [OVERALL] rates
                                           (straggler-exclusive)
  [OP] WeightedAverageLatency(us)          ops-weighted mean
  [OP] MaxOver{95,99}thPercentileLatency   max of per-writer percentiles,
                                           deliberately conservative

Extra engine-specific lines (LoadConvergenceTime, the [CONSISTENCY] section)
are appended through extra_lines. Parsers that do not know them ignore them.
"""

import os
import re
from datetime import datetime

_YCSB_LINE = re.compile(r"^\[([^\]]+)\],\s*([^,]+?),\s*(.+?)\s*$")

# Cross-node aggregation groups per-writer files by this name contract.
PERWRITER_RE = re.compile(
    r"^(?P<ks>[^-]+)-(?P<opcnt>\d+)-(?P<rc>\d+)-workload(?P<wl>[^-]+)-"
    r"(?P<db>.+?)_w(?P<widx>\d+)of(?P<total>\d+)_t(?P<thread>\d+)"
    r"_trial(?P<trial>\d+)\.result$"
)


def load_writer_file(ks, rc, db, writer_idx, num_writers, thread):
    return f"{ks}-{rc}-insert-{db}_w{writer_idx}of{num_writers}_t{thread}.result"


def load_agg_file(ks, rc, db, num_writers, thread):
    return f"{ks}-{rc}-insert-{db}_agg_w{num_writers}_t{thread}.result"


def run_writer_file(ks, opcnt, rc, wl, db, writer_idx, total_writers, thread, trial):
    return (
        f"{ks}-{opcnt}-{rc}-workload{wl}-{db}"
        f"_w{writer_idx}of{total_writers}_t{thread}_trial{trial}.result"
    )


def run_agg_file(ks, opcnt, rc, wl, db, num_writers, total_writers, offset, thread, trial):
    if num_writers == total_writers and offset == 0:
        agg_tag = f"_agg_w{total_writers}"
    else:
        agg_tag = f"_agg_w{num_writers}of{total_writers}_off{offset}"
    return f"{ks}-{opcnt}-{rc}-workload{wl}-{db}{agg_tag}_t{thread}_trial{trial}.result"


def multinode_agg_file(ks, opcnt, rc, wl, db, total_writers, thread, trial):
    return (
        f"{ks}-{opcnt}-{rc}-workload{wl}-{db}"
        f"_agg_multinode_w{total_writers}_t{thread}_trial{trial}.result"
    )


def parse_ycsb_result(path):
    """Return {section: {metric: value_str}} parsed from a YCSB result file."""
    sections = {}
    if not os.path.exists(path):
        return sections
    with open(path, "r", errors="replace") as f:
        for line in f:
            m = _YCSB_LINE.match(line.strip())
            if not m:
                continue
            section, metric, value = m.group(1), m.group(2).strip(), m.group(3).strip()
            sections.setdefault(section, {})[metric] = value
    return sections


def _fnum(d, key):
    try:
        return float(d.get(key, ""))
    except (TypeError, ValueError):
        return None


def write_aggregate(agg_path, per_writer_files, wall_ms, num_writers, extra_lines=None):
    """Parse the per-writer YCSB outputs and write an aggregate summary."""
    parsed = [(p, parse_ycsb_result(p)) for p in per_writer_files]

    op_sections = set()
    for _, sec in parsed:
        for k in sec.keys():
            if k != "OVERALL":
                op_sections.add(k)

    total_ops = 0
    writer_runtimes_ms = []
    writer_throughputs = []
    for _, sec in parsed:
        overall = sec.get("OVERALL", {})
        rt = _fnum(overall, "RunTime(ms)")
        tp = _fnum(overall, "Throughput(ops/sec)")
        if rt is not None:
            writer_runtimes_ms.append(rt)
        if tp is not None:
            writer_throughputs.append(tp)
        for op in op_sections:
            ops = _fnum(sec.get(op, {}), "Operations")
            if ops is not None:
                total_ops += ops

    agg_lines = []
    agg_lines.append(f"[AGGREGATE], NumWriters, {num_writers}")
    agg_lines.append(f"[AGGREGATE], WallTime(ms), {int(wall_ms)}")
    agg_lines.append(f"[AGGREGATE], TotalOperations, {int(total_ops)}")
    if wall_ms > 0:
        agg_lines.append(
            f"[AGGREGATE], Throughput(ops/sec), {total_ops / (wall_ms / 1000.0):.2f}"
        )
    if writer_throughputs:
        agg_lines.append(
            f"[AGGREGATE], SumWriterThroughput(ops/sec), {sum(writer_throughputs):.2f}"
        )
        agg_lines.append(
            f"[AGGREGATE], AvgWriterThroughput(ops/sec), "
            f"{sum(writer_throughputs) / len(writer_throughputs):.2f}"
        )
    if writer_runtimes_ms:
        agg_lines.append(
            f"[AGGREGATE], MaxWriterRunTime(ms), {int(max(writer_runtimes_ms))}"
        )
        agg_lines.append(
            f"[AGGREGATE], MinWriterRunTime(ms), {int(min(writer_runtimes_ms))}"
        )

    for op in sorted(op_sections):
        ops_sum = 0.0
        lat_weighted = 0.0
        max_p99 = 0.0
        max_p95 = 0.0
        max_max_lat = 0.0
        any_data = False
        for _, sec in parsed:
            s = sec.get(op, {})
            ops = _fnum(s, "Operations")
            if ops is None:
                continue
            any_data = True
            ops_sum += ops
            avg = _fnum(s, "AverageLatency(us)")
            if avg is not None:
                lat_weighted += avg * ops
            p95 = _fnum(s, "95thPercentileLatency(us)")
            p99 = _fnum(s, "99thPercentileLatency(us)")
            mx = _fnum(s, "MaxLatency(us)")
            if p95 is not None and p95 > max_p95:
                max_p95 = p95
            if p99 is not None and p99 > max_p99:
                max_p99 = p99
            if mx is not None and mx > max_max_lat:
                max_max_lat = mx
        if not any_data:
            continue
        agg_lines.append(f"[{op}], Operations, {int(ops_sum)}")
        if ops_sum > 0:
            agg_lines.append(
                f"[{op}], WeightedAverageLatency(us), {lat_weighted / ops_sum:.2f}"
            )
        agg_lines.append(f"[{op}], MaxOver95thPercentileLatency(us), {int(max_p95)}")
        agg_lines.append(f"[{op}], MaxOver99thPercentileLatency(us), {int(max_p99)}")
        agg_lines.append(f"[{op}], MaxMaxLatency(us), {int(max_max_lat)}")

    if extra_lines:
        agg_lines.extend(extra_lines)

    agg_lines.append("")
    agg_lines.append("# Per-writer result files:")
    for p in per_writer_files:
        agg_lines.append(f"#   {p}")

    with open(agg_path, "w") as f:
        f.write(
            f"# Aggregated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
            f"across {num_writers} writer processes\n"
        )
        f.write("\n".join(agg_lines) + "\n")
    print(f"[aggregate] wrote {agg_path}")
