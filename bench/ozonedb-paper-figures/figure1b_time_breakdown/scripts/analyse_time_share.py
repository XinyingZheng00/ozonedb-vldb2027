import subprocess
import re
import sys

REOPEN_DATA = "perf_updaterandom_reopen.data"
BASE_DATA = "perf_updaterandom_no_reopen.data"
THREADS = 4

# Categorize the samples with the output of `perf report --call-graph folded`.
# The script classifies each sampled call chain by its *leaf* symbol. It adds up:
#   a = Open+Close (classified sum)
#   b = Operations  (classified sum)
# It reports the normalized shares a/(a+b) and b/(a+b).
#
# Important: the symbol names depend on the build, so these lists are heuristic.
OPEN_PATTERNS = [
    "rocksdb::Benchmark::OpenDbWithRetry",
    "rocksdb::DefaultHooks::Open",
    "rocksdb::DB::Open",
    "rocksdb::DBImpl::Open",
    "TryOpenDb",
]
CLOSE_PATTERNS = [
    "DeleteDBs",
    "DestroyDB",
    "CloseImpl",
    "CloseHelper",
    "rocksdb::DBImpl::~DBImpl",
    "rocksdb::DB::~DB",
    "~DBImpl",
    "~DB",
]
OPS_PATTERNS = [
    # Put/Get are usually the leaves for this workload
    "rocksdb::DBImpl::Get",
    "rocksdb::DBImpl::Put",
    "rocksdb::DB::Get",
    "rocksdb::DB::Put",
    # Also allow the benchmark wrapper itself
    "rocksdb::Benchmark::UpdateRandom",
]


def compute_group_pcts_from_folded(path):
    """
    Use the folded call-graph output and classify by the *leaf* symbol (last frame).
    This avoids the double-count problems of "Children%". It also counts open/close
    that happens outside UpdateRandom.
    """

    cmd = [
        "sudo",
        "perf",
        "report",
        "-i",
        path,
        "--comms=db_bench",
        "--stdio",
        "--call-graph",
        "folded,0.01,200,caller,function,percent",
        "--percent-limit",
        "0",
        "--percentage",
        "absolute",
        "--hide-unresolved",
        "--sort",
        "symbol",
    ]

    open_pct = 0.0
    close_pct = 0.0
    ops_pct = 0.0

    # A typical line looks like this (see your earlier output):
    #   0.35% start_thread;rocksdb::...;rocksdb::DumpDBFileSummary;rocksdb::ParseFileName
    line_re = re.compile(r"^\s*([0-9.]+)%\s+(.*)$")

    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    for line in p.stdout:
        m = line_re.match(line)
        if not m:
            continue
        pct = float(m.group(1))
        chain = m.group(2).strip()
        if not chain:
            continue

        frames = [f.strip() for f in chain.split(";") if f.strip()]
        if not frames:
            continue
        leaf = frames[-1]

        if any(pat in leaf for pat in CLOSE_PATTERNS):
            close_pct += pct
        elif any(pat in leaf for pat in OPEN_PATTERNS):
            open_pct += pct
        elif any(pat in leaf for pat in OPS_PATTERNS):
            ops_pct += pct

    p.wait()
    return open_pct, close_pct, ops_pct


def breakdown(path, label):
    open_pct, close_pct, ops_pct = compute_group_pcts_from_folded(path)
    a = open_pct + close_pct
    b = ops_pct
    denom = a + b

    open_share = 100.0 * a / denom if denom > 0 else 0.0
    ops_share = 100.0 * b / denom if denom > 0 else 0.0

    title = "Reopen" if label == "reopen" else "Baseline (no reopen)"
    print(f"{title}: Open/Close vs Operations (THREADS={THREADS})")
    print(f"\tOpen classified sum:  {open_pct:.6f}%")
    print(f"\tClose classified sum: {close_pct:.6f}%")
    print(f"\tOperations classified sum: {ops_pct:.6f}%")
    print(f"\ta+b classified total:  {denom:.6f}% (note: may not equal 100 due to perf call-graph accounting)")
    print(f"\tShares within (a+b): Open/Close {open_share:.4f}%, Ops {ops_share:.4f}%")


breakdown(REOPEN_DATA, "reopen")
print()
breakdown(BASE_DATA, "base")