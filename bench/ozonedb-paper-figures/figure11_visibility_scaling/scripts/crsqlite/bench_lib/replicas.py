"""Replica-file layout and direct (extension-free) inspection.

The layout matches the ozonedb cached/run split, so sweeps can reuse one load:

    {ycsb_data_path}/cached_data-{db}-{ks}-{rc}-w{i}/replica.db      load output
    {ycsb_data_path}/{db}-{ks}-workload{wl}-{opcnt}-w{i}/replica.db  run scratch

A converged cached replica holds the FULL dataset. So a run with fewer writers
than the load can reuse a subset of the cached replicas. Each run writer copies
ITS OWN cached dir, never another writer's. A cr-sqlite replica's identity is
its site_id. A copy of one file to two writers would clone the site_id and
corrupt the CRDT merge.

Reads here use plain sqlite3 with no extension. SELECTs do not fire the crsql
triggers, so divergence counting works even where python lacks
enable_load_extension (macOS dev machines).
"""

import os
import sqlite3
import subprocess

REPLICA_FILE = "replica.db"


def cached_dir(data_path, db_name, ks, rc, writer_idx):
    return os.path.join(data_path, f"cached_data-{db_name}-{ks}-{rc}-w{writer_idx}")


def run_dir(data_path, db_name, ks, wl, opcnt, writer_idx):
    return os.path.join(data_path, f"{db_name}-{ks}-workload{wl}-{opcnt}-w{writer_idx}")


def replica_db(dirname):
    return os.path.join(dirname, REPLICA_FILE)


def fresh_dir(dirname):
    subprocess.run(["rm", "-rf", dirname])
    os.makedirs(dirname, exist_ok=True)


def copy_cached_to_run(cached, run):
    subprocess.run(["rm", "-rf", run])
    subprocess.run(["cp", "-r", cached, run])


def _open_ro(db_path):
    # This is a plain open, not mode=ro. Read-only opens of a live WAL database
    # get awkward around the -shm file, and nothing here ever writes.
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def count_rows(db_path):
    conn = _open_ro(db_path)
    try:
        return conn.execute("SELECT count(*) FROM usertable").fetchone()[0]
    finally:
        conn.close()


def divergent_keys(db_a, db_b):
    """Walk two replicas in key order as a streaming merge. Return the number
    of keys that are present in only one replica or that hold different bytes."""
    ca = _open_ro(db_a)
    cb = _open_ro(db_b)
    try:
        ita = ca.execute("SELECT ycsb_key, field_data FROM usertable ORDER BY ycsb_key")
        itb = cb.execute("SELECT ycsb_key, field_data FROM usertable ORDER BY ycsb_key")
        ra, rb = next(ita, None), next(itb, None)
        divergent = 0
        while ra is not None or rb is not None:
            if ra is None:
                divergent += 1
                rb = next(itb, None)
            elif rb is None:
                divergent += 1
                ra = next(ita, None)
            elif ra[0] < rb[0]:
                divergent += 1
                ra = next(ita, None)
            elif ra[0] > rb[0]:
                divergent += 1
                rb = next(itb, None)
            else:
                va = bytes(ra[1]) if ra[1] is not None else None
                vb = bytes(rb[1]) if rb[1] is not None else None
                if va != vb:
                    divergent += 1
                ra, rb = next(ita, None), next(itb, None)
        return divergent
    finally:
        ca.close()
        cb.close()


def max_divergence(db_paths):
    """Return the max pairwise divergence of each replica against the first one."""
    if len(db_paths) < 2:
        return 0
    return max(divergent_keys(db_paths[0], p) for p in db_paths[1:])
