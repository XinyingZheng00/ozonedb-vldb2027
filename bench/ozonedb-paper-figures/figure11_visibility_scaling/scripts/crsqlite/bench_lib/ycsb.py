"""Build and spawn YCSB processes for the crsqlite binding.

This module bypasses bin/ycsb, the same as the ozonedb multiproc runners. The
parent process resolves the classpath ONCE through Maven. Then N writer
processes invoke site.ycsb.Client directly. So parallel writers never race on
~/.m2 or the binding's target/ directory.
"""

import os
import subprocess
import time

BINDING = "crsqlite"
DB_CLASSNAME = "site.ycsb.db.crsqlite.CrSQLiteClient"

_classpath_cache = {}


def crsqlite_home():
    home = os.environ.get("CRSQLITE_HOME")
    if not home:
        raise EnvironmentError("CRSQLITE_HOME environment variable is not set.")
    return home


def ycsb_path():
    return os.path.join(crsqlite_home(), "ycsb")


def resolve_classpath():
    """Resolve the full YCSB classpath for the crsqlite binding. Run Maven
    once, serially, in the parent process."""
    if BINDING in _classpath_cache:
        return _classpath_cache[BINDING]
    project = BINDING + "-binding"
    print(f"[classpath] resolving {project} via Maven (one-time, serial)...")
    out = subprocess.check_output(
        [
            "mvn",
            "-pl", f"site.ycsb:{project}",
            "-am",
            "package",
            "-DskipTests",
            "dependency:build-classpath",
            "-DincludeScope=compile",
            "-Dmdep.outputFilterFile=true",
        ],
        cwd=ycsb_path(),
        text=True,
    )
    cp_lines = [ln for ln in out.splitlines() if ln.startswith("classpath=")]
    if not cp_lines:
        raise RuntimeError(f"Maven produced no classpath= line for {project}")
    maven_cp = cp_lines[-1][len("classpath="):]

    target_dir = os.path.join(ycsb_path(), BINDING, "target")
    target_jars = []
    if os.path.isdir(target_dir):
        for root, _dirs, files in os.walk(target_dir):
            for fn in files:
                if fn.endswith(".jar") and fn.startswith(project):
                    target_jars.append(os.path.join(root, fn))

    classpath = os.pathsep.join(target_jars + [maven_cp])
    _classpath_cache[BINDING] = classpath
    print(f"[classpath] {project} resolved ({len(target_jars)} target jar(s) + mvn deps)")
    return classpath


def java_binary():
    jh = os.environ.get("JAVA_HOME")
    if jh:
        return os.path.join(jh, "bin", "java")
    return "java"


def partition_records(total, num_writers):
    """Split `total` into num_writers contiguous (start, count) pairs."""
    chunk = total // num_writers
    parts = []
    offset = 0
    for i in range(num_writers):
        count = chunk if i < num_writers - 1 else (total - offset)
        parts.append((offset, count))
        offset += count
    return parts


def crsqlite_props(cfg, db_path, extension, sync_endpoint=None):
    """Return the -p flags for the binding, from the crsqlite: section of ycsb.yaml.

    sync_endpoint is the host:port of THIS writer's own sync daemon. If you
    pass it, it turns on fenced reads (crsqlite.read_barrier). Then every read
    barriers through the daemon's /barrier before it touches the replica."""
    c = cfg.get("crsqlite") or {}
    props = [
        ("crsqlite.db_path", db_path),
        ("crsqlite.extension", extension),
        ("crsqlite.journal_mode", c.get("journal_mode", "WAL")),
        ("crsqlite.synchronous", c.get("synchronous", "FULL")),
        ("crsqlite.cache_size", str(c.get("cache_size", -163840))),
        ("crsqlite.busy_timeout_ms", str(c.get("busy_timeout_ms", 30000))),
    ]
    if sync_endpoint:
        props += [
            ("crsqlite.read_barrier", "true"),
            ("crsqlite.sync_endpoint", sync_endpoint),
        ]
    out = []
    for k, v in props:
        out += ["-p", f"{k}={v}"]
    return out


def build_cmd(classpath, workload_path, thread, common_props, phase_args):
    """Return one writer's java invocation. phase_args is ["-load"] plus the
    insertstart/insertcount props for the load phase. For the run phase it is
    ["-t"] plus operationcount/maxexecutiontime."""
    return (
        [
            java_binary(),
            "-cp", classpath,
            "site.ycsb.Client",
            "-db", DB_CLASSNAME,
            "-threads", str(thread),
            "-s",
            "-P", workload_path,
        ]
        + common_props
        + phase_args
    )


def spawn_parallel(jobs):
    """Launch the (cmd, stdout_path) pairs in parallel. Return (rcs, wall_ms)."""
    procs = []
    fds = []
    wall_start = time.time()
    for cmd, out_path in jobs:
        subprocess.run(["rm", "-rf", out_path])
        f = open(out_path, "a")
        fds.append(f)
        print(" ".join(cmd))
        procs.append(subprocess.Popen(cmd, stdout=f, stderr=f))
    rcs = [p.wait() for p in procs]
    wall_ms = (time.time() - wall_start) * 1000.0
    for f in fds:
        f.close()
    if any(r != 0 for r in rcs):
        print(f"[warning] writer return codes: {rcs}")
    return rcs, wall_ms
