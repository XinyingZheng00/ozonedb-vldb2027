#!/usr/bin/env python3
"""One worker process for the 'sqlite_rest' (re-architected serval) trial.
Genuinely a separate OS process (spawned via subprocess.Popen by the
orchestrator, not a thread) -- issues real HTTP requests to the ONE
running servald daemon's REST API (POST /restful/rhizome/insert, GET
.../decrypted.bin), the real implementation of the "single process
performs all Rhizome database operations" fix proposed in serval-dna
issue #1. Writes one JSON line per cycle to --out.
"""
import argparse
import json
import os
import sys
import time

import requests


def cycle(base_url, auth, worker_id, seq):
    content = f"worker={worker_id} seq={seq} payload={os.urandom(16).hex()}\n".encode()
    manifest_text = f"service=file\nname=w{worker_id}_{seq}.txt\n"
    files = [
        ("manifest", (f"m{worker_id}_{seq}", manifest_text, "rhizome/manifest;format=text+binarysig")),
        ("payload", (f"w{worker_id}_{seq}.txt", content, "application/octet-stream")),
    ]

    t0 = time.time()
    try:
        r = requests.post(f"{base_url}/restful/rhizome/insert", auth=auth, files=files, timeout=30)
    except requests.RequestException as e:
        return {"ok": False, "stage": "add", "t_add": time.time() - t0, "t_extract": None, "error": str(e)[:300]}
    t_add = time.time() - t0
    if r.status_code != 201:
        return {"ok": False, "stage": "add", "t_add": t_add, "t_extract": None,
                "error": f"status={r.status_code} body={r.text[:200]}"}

    bid = r.headers.get("Serval-Rhizome-Bundle-Id")
    if not bid:
        return {"ok": False, "stage": "add-parse", "t_add": t_add, "t_extract": None, "error": "no bundle id header"}

    t1 = time.time()
    try:
        r2 = requests.get(f"{base_url}/restful/rhizome/{bid}/decrypted.bin", auth=auth, timeout=30)
    except requests.RequestException as e:
        return {"ok": False, "stage": "extract", "t_add": t_add, "t_extract": time.time() - t1, "error": str(e)[:300]}
    t_extract = time.time() - t1
    if r2.status_code != 200:
        return {"ok": False, "stage": "extract", "t_add": t_add, "t_extract": t_extract,
                "error": f"status={r2.status_code} body={r2.text[:200]}"}

    if r2.content != content:
        return {"ok": False, "stage": "verify-mismatch", "t_add": t_add, "t_extract": t_extract, "error": "content mismatch"}

    return {"ok": True, "stage": None, "t_add": t_add, "t_extract": t_extract, "error": None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--worker-id", type=int, required=True)
    ap.add_argument("--cycles", type=int, default=None,
                     help="fixed cycle count (mutually exclusive with --duration-s)")
    ap.add_argument("--duration-s", type=float, default=None,
                     help="run cycles back-to-back until this many seconds elapse")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if (args.cycles is None) == (args.duration_s is None):
        ap.error("specify exactly one of --cycles or --duration-s")

    auth = (args.user, args.password)
    if args.cycles is not None:
        rows = [cycle(args.base_url, auth, args.worker_id, seq) for seq in range(args.cycles)]
    else:
        deadline = time.time() + args.duration_s
        rows = []
        seq = 0
        while time.time() < deadline:
            rows.append(cycle(args.base_url, auth, args.worker_id, seq))
            seq += 1
    with open(args.out, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
