#!/usr/bin/env python3
"""Anti-entropy replicator for one cr-sqlite replica.

One daemon runs next to every replica file. It serves that replica's change
log over HTTP and pulls the changes of its peers on a fixed interval. This is
the classic cr-sqlite sync pattern, built on the crsql_changes virtual table:

    pull:   SELECT "table", pk, cid, val, col_version, db_version, site_id,
                   cl, seq
            FROM crsql_changes WHERE db_version > ? AND site_id != ?
    apply:  INSERT INTO crsql_changes (...) VALUES (...)

Replication is therefore asynchronous and best-effort by construction. A
write is acknowledged to YCSB as soon as the local SQLite commit returns.
Other replicas see it only after the next pull round. `--interval-ms` is the
staleness knob that the consistency experiments sweep.

Fenced reads: /barrier runs one full pull round that STARTS after the
request arrives. The round drains every peer to the frontier that it
reported at the round start. So a read that is issued after a successful
barrier observes every transaction that any peer had committed before the
barrier began. This is the cr-sqlite analog of ozonedb's linearizable_reads
fence, built from the same pull/apply primitives. If any peer is
unreachable, the barrier fails (HTTP 500). A partial round must never
masquerade as freshness.

Endpoints (all GET, JSON):
    /changes?since=<db_version>&exclude=<site_id_hex>[&batch=<n>]
        Changes with db_version > since, without the rows that originated at
        `exclude`. So a peer never gets its own writes back. The response
        includes the current db_version of the serving replica, so an empty
        response still advances the caller's cursor. Batches are cut only at
        db_version boundaries, so a cursor can never skip half a transaction.
    /status   {rows, db_version, site_id}   cheap. Used for convergence polling
    /hash     {rows, hash}                  md5 over (key, value) in key order
    /barrier  {rows_applied, round_ms, peers}   fenced-read pull round (above)
    /barrier_stats  {count, mean_ms, p50_ms, p95_ms, p99_ms, max_ms}
        The distribution of completed /barrier round times since startup. The
        run harness scrapes it once per run for the [SYNCREAD] lines.
    /round_stats[?reset=1]
        {count, nominal_interval_ms, period_mean_ms, rounds_per_sec,
         mean_ms, p50_ms, p95_ms, p99_ms, max_ms, rows_applied,
         pull_failures, window_s}
        Background (interval) rounds since startup or since the last reset.
        The loop sleeps only the REMAINDER of --interval-ms after each round.
        So once a round takes longer than the interval, the achieved period
        (period_mean_ms) is the real staleness knob, not the nominal one. At
        63 peers "10 ms" is nominal. The run harness resets this window when
        the writers start and scrapes it when they finish, for the
        [CONSISTENCY] SyncRound*/SyncPeriodMean lines.

Per-ROUND metrics are appended to --metrics as CSV, one row per completed
round. The reason is "interval" for the background loop and "barrier" for
fenced-read rounds. One row per peer per pull was about 8 GB per 300 s run
at 64 writers x 10 ms. Per round it is a few thousand rows.
    ts,reason,peers,peers_failed,rows_applied,fetch_max_ms,apply_ms,round_ms
fetch_max_ms is the fetch time of the slowest peer. Peers are fetched in
parallel, so it approximates the network critical path of the round.
apply_ms is the sum of the apply time across peers. Applies are serialized.

Usage:
    python3 sync_daemon.py --db /tank/crsqlite/.../replica.db --port 7801 \
        --peers 127.0.0.1:7802,127.0.0.1:7803 --interval-ms 100 \
        --extension "$CRSQLITE_HOME/lib/crsqlite" --metrics /path/w0.csv
"""

import argparse
import base64
import hashlib
import json
import os
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

CHANGES_COLS = '"table", "pk", "cid", "val", "col_version", "db_version", "site_id", "cl", "seq"'


def log(msg):
    print(f"[syncd {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def open_replica(db_path, extension, busy_timeout_ms, synchronous):
    """Open a connection with the crsqlite extension loaded. The extension
    must be the first thing loaded on a fresh connection."""
    conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
    if not hasattr(conn, "enable_load_extension"):
        raise RuntimeError(
            "this python's sqlite3 lacks enable_load_extension(); "
            "run on the Ubuntu bench nodes (or a python built with extension support)"
        )
    conn.enable_load_extension(True)
    conn.load_extension(extension)
    conn.enable_load_extension(False)
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA synchronous = {synchronous}")
    return conn


def ensure_schema(conn):
    """Create usertable and promote it to a CRR, if that is not already done.
    The daemon may start before the YCSB binding first touches the replica.
    An apply of changes for a table that does not exist yet would fail."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS usertable "
            "(ycsb_key TEXT PRIMARY KEY NOT NULL, field_data BLOB)"
        )
        is_crr = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'usertable__crsql_clock'"
        ).fetchone()[0]
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    if not is_crr:
        # crsql_as_crr manages its own transaction.
        conn.execute("SELECT crsql_as_crr('usertable')")


# ---------------------------------------------------------------- encoding --
# crsql_changes values can be NULL, INTEGER, REAL, TEXT, or BLOB. JSON cannot
# carry bytes, so every value goes over the wire with a tag.

def enc_val(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return ["i", int(v)]
    if isinstance(v, int):
        return ["i", v]
    if isinstance(v, float):
        return ["f", v]
    if isinstance(v, str):
        return ["s", v]
    if isinstance(v, (bytes, memoryview)):
        return ["b", base64.b64encode(bytes(v)).decode("ascii")]
    raise TypeError(f"unencodable sqlite value: {type(v)}")


def dec_val(e):
    if e is None:
        return None
    tag, v = e
    if tag == "i":
        return int(v)
    if tag == "f":
        return float(v)
    if tag == "s":
        return v
    if tag == "b":
        return base64.b64decode(v)
    raise ValueError(f"unknown value tag: {tag}")


def enc_row(row):
    table, pk, cid, val, col_version, db_version, site_id, cl, seq = row
    return [
        table,
        base64.b64encode(bytes(pk)).decode("ascii"),
        cid,
        enc_val(val),
        col_version,
        db_version,
        bytes(site_id).hex(),
        cl,
        seq,
    ]


def dec_row(row):
    table, pk_b64, cid, val, col_version, db_version, site_hex, cl, seq = row
    return (
        table,
        base64.b64decode(pk_b64),
        cid,
        dec_val(val),
        col_version,
        db_version,
        bytes.fromhex(site_hex),
        cl,
        seq,
    )


# ------------------------------------------------------------------ daemon --

class Daemon:
    def __init__(self, args):
        self.args = args
        self.stop_event = threading.Event()
        # There are two connections. HTTP handler threads share read_conn
        # under read_lock. write_conn applies the pulled changes. round_lock
        # serializes the rounds (background interval or /barrier). Within a
        # round each peer has exactly one worker. apply_lock guards
        # write_conn, the cursors, and the metrics file across those workers.
        self.read_conn = open_replica(
            args.db, args.extension, args.busy_timeout_ms, args.synchronous
        )
        self.read_lock = threading.Lock()
        self.write_conn = open_replica(
            args.db, args.extension, args.busy_timeout_ms, args.synchronous
        )
        ensure_schema(self.write_conn)
        self.site_id = bytes(
            self.write_conn.execute("SELECT crsql_site_id()").fetchone()[0]
        )
        self.cursors = {peer: 0 for peer in args.peers}
        # --assume-converged: the cursors are stamped from every peer's
        # /status BEFORE ready flips true (see bootstrap_cursors). Until then
        # rounds must not run, and /barrier refuses. Callers gate their
        # writers on ready. That makes the stamp happen strictly before any
        # write. That property is what makes it safe to skip the pre-start
        # history.
        self.ready = not (args.assume_converged and args.peers)
        self.round_lock = threading.Lock()
        self.apply_lock = threading.Lock()
        self.barrier_ms = []
        self.barrier_lock = threading.Lock()
        # Background-round window for /round_stats (see module docstring).
        self.round_stats_lock = threading.Lock()
        self._reset_round_window()
        self.metrics_f = None
        if args.metrics:
            os.makedirs(os.path.dirname(args.metrics) or ".", exist_ok=True)
            new = not os.path.exists(args.metrics)
            self.metrics_f = open(args.metrics, "a")
            if new:
                self.metrics_f.write(
                    "ts,reason,peers,peers_failed,rows_applied,"
                    "fetch_max_ms,apply_ms,round_ms\n"
                )

    def _reset_round_window(self):
        """The caller holds round_stats_lock, or the caller is __init__."""
        self.round_ms = []
        self.round_starts = []
        self.round_rows = 0
        self.round_failures = 0
        self.round_window_start = time.time()

    # ------------------------------------------------------------- serving --

    def db_version(self, conn):
        return int(conn.execute("SELECT crsql_db_version()").fetchone()[0])

    def changes_since(self, since, exclude_site, batch):
        """Return the rows with db_version > since. Never cut a db_version
        group in half. If the LIMIT lands mid-group, a follow-up query fetches
        the rest of that group. So `max(db_version)` is always a safe cursor."""
        where = 'db_version > ?'
        params = [since]
        if exclude_site is not None:
            where += " AND site_id != ?"
            params.append(exclude_site)
        rows = self.read_conn.execute(
            f"SELECT {CHANGES_COLS} FROM crsql_changes WHERE {where} "
            f'ORDER BY db_version ASC, seq ASC LIMIT {int(batch)}',
            params,
        ).fetchall()
        if len(rows) == int(batch) and rows:
            last_dv, last_seq = rows[-1][5], rows[-1][8]
            tail_where = where + " AND db_version = ? AND seq > ?"
            rows += self.read_conn.execute(
                f"SELECT {CHANGES_COLS} FROM crsql_changes WHERE {tail_where} "
                f'ORDER BY seq ASC',
                params + [last_dv, last_seq],
            ).fetchall()
        return rows

    @staticmethod
    def _distribution(xs):
        xs = sorted(xs)
        n = len(xs)

        def pct(p):
            return round(xs[min(n - 1, int(p / 100.0 * n))], 3) if n else 0.0

        return {
            "count": n,
            "mean_ms": round(sum(xs) / n, 3) if n else 0.0,
            "p50_ms": pct(50),
            "p95_ms": pct(95),
            "p99_ms": pct(99),
            "max_ms": round(xs[-1], 3) if n else 0.0,
        }

    def barrier_stats(self):
        with self.barrier_lock:
            xs = list(self.barrier_ms)
        return self._distribution(xs)

    def round_stats(self, reset=False):
        """Return the background-round distribution over the current window.
        With reset=True the window restarts AFTER the snapshot is taken."""
        now = time.time()
        with self.round_stats_lock:
            xs = list(self.round_ms)
            starts = list(self.round_starts)
            rows = self.round_rows
            failures = self.round_failures
            window_s = now - self.round_window_start
            if reset:
                self._reset_round_window()
        out = self._distribution(xs)
        n = len(starts)
        out.update({
            "nominal_interval_ms": self.args.interval_ms,
            # Achieved anti-entropy period: mean gap between round starts.
            "period_mean_ms": (
                round((starts[-1] - starts[0]) / (n - 1) * 1000.0, 3) if n >= 2 else 0.0
            ),
            "rounds_per_sec": round(n / window_s, 3) if window_s > 0 else 0.0,
            "rows_applied": rows,
            "pull_failures": failures,
            "window_s": round(window_s, 3),
        })
        return out

    def handle(self, path, query):
        if path == "/changes":
            since = int(query.get("since", ["0"])[0])
            batch = int(query.get("batch", [str(self.args.batch)])[0])
            exclude = query.get("exclude", [None])[0]
            exclude_b = bytes.fromhex(exclude) if exclude else None
            with self.read_lock:
                # Read db_version BEFORE the row select. A change that commits
                # in between then shows up in `rows`, because its db_version
                # > dv. So an empty-rows cursor jump to dv can never skip it.
                dv = self.db_version(self.read_conn)
                rows = self.changes_since(since, exclude_b, batch)
            return {
                "site_id": self.site_id.hex(),
                "db_version": dv,
                "changes": [enc_row(r) for r in rows],
            }
        if path == "/status":
            with self.read_lock:
                rows = self.read_conn.execute(
                    "SELECT count(*) FROM usertable"
                ).fetchone()[0]
                dv = self.db_version(self.read_conn)
            return {"rows": rows, "db_version": dv, "site_id": self.site_id.hex(),
                    "ready": self.ready}
        if path == "/hash":
            h = hashlib.md5()
            n = 0
            with self.read_lock:
                cur = self.read_conn.execute(
                    "SELECT ycsb_key, field_data FROM usertable ORDER BY ycsb_key"
                )
                for key, val in cur:
                    h.update(key.encode("utf-8"))
                    h.update(b"\x00")
                    h.update(bytes(val) if val is not None else b"")
                    h.update(b"\x01")
                    n += 1
            return {"rows": n, "hash": h.hexdigest()}
        if path == "/barrier":
            if not self.ready:
                # A fenced read before the cursors are stamped could observe a
                # frontier that we then skip past. Refuse rather than lie.
                raise RuntimeError("bootstrapping cursors; not ready")
            # round_once serializes on round_lock. So the round that this
            # request runs STARTS after the request arrived. The wait for an
            # in-flight round is part of the fenced read's latency.
            t0 = time.time()
            rows = self.round_once("barrier", strict=True)
            round_ms = (time.time() - t0) * 1000.0
            with self.barrier_lock:
                self.barrier_ms.append(round_ms)
            return {
                "rows_applied": rows,
                "round_ms": round(round_ms, 3),
                "peers": len(self.args.peers),
            }
        if path == "/barrier_stats":
            return self.barrier_stats()
        if path == "/round_stats":
            reset = query.get("reset", ["0"])[0] in ("1", "true", "yes")
            return self.round_stats(reset=reset)
        return None

    # ------------------------------------------------------------- pulling --

    def drain_peer(self, peer, reason, drain):
        """Pull from one peer. With drain=False (background interval), do one
        fetch and apply. That is exactly the historical per-round behavior.
        With drain=True (barrier), loop until the cursor reaches the frontier
        that the peer reported on the FIRST fetch of this round. So a backlog
        larger than one batch cannot leak through a fenced read.

        The caller holds round_lock. This thread therefore owns this peer's
        cursor for the duration. Only write_conn needs apply_lock. The peer
        workers of the same round share it.

        Return (rows_applied, fetch_ms, apply_ms) for this peer, summed over
        the drain iterations. round_once folds them into one metrics row per
        round."""
        total = 0
        fetch_total_ms = 0.0
        apply_total_ms = 0.0
        frontier = None
        while True:
            since = self.cursors[peer]
            t0 = time.time()
            url = (
                f"http://{peer}/changes?since={since}"
                f"&exclude={self.site_id.hex()}&batch={self.args.batch}"
            )
            with urlopen(url, timeout=self.args.http_timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            fetch_total_ms += (time.time() - t0) * 1000.0
            if frontier is None:
                frontier = payload["db_version"]

            rows = [dec_row(r) for r in payload["changes"]]
            t1 = time.time()
            with self.apply_lock:
                if rows:
                    self.write_conn.execute("BEGIN IMMEDIATE")
                    try:
                        self.write_conn.executemany(
                            f"INSERT INTO crsql_changes ({CHANGES_COLS}) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            rows,
                        )
                        self.write_conn.execute("COMMIT")
                    except Exception:
                        self.write_conn.execute("ROLLBACK")
                        raise
                    self.cursors[peer] = max(
                        self.cursors[peer], max(r[5] for r in rows)
                    )
                else:
                    # Nothing below the peer's head concerns us. Jump the
                    # cursor, so that the WHERE clause of the next pull stays
                    # cheap.
                    self.cursors[peer] = max(self.cursors[peer], payload["db_version"])
                apply_total_ms += (time.time() - t1) * 1000.0
            total += len(rows)
            if not drain or not rows or self.cursors[peer] >= frontier:
                return total, fetch_total_ms, apply_total_ms

    def round_once(self, reason, strict):
        """Run one full pull round over every peer. Fetch the peers in
        parallel. The round cost is then about the max peer RTT, not the sum.
        Otherwise fenced reads would be penalized O(writers). strict=True
        (barrier) raises if ANY peer fails. strict=False (background) logs the
        failure and moves on."""
        with self.round_lock:
            peers = self.args.peers
            if not peers:
                return 0
            t_round = time.time()
            results = {}   # peer maps to (rows, fetch_ms, apply_ms)
            errors = {}

            def work(peer):
                try:
                    results[peer] = self.drain_peer(
                        peer, reason, drain=(reason == "barrier")
                    )
                except Exception as e:
                    errors[peer] = e

            if len(peers) == 1:
                work(peers[0])
            else:
                threads = [
                    threading.Thread(target=work, args=(p,)) for p in peers
                ]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

            rows_applied = sum(r[0] for r in results.values())
            fetch_max_ms = max((r[1] for r in results.values()), default=0.0)
            apply_ms = sum(r[2] for r in results.values())
            round_ms = (time.time() - t_round) * 1000.0
            # Write one metrics row per round. round_lock serializes the
            # rounds, so the file needs no further locking.
            if self.metrics_f:
                self.metrics_f.write(
                    f"{datetime.now().isoformat()},{reason},{len(peers)},"
                    f"{len(errors)},{rows_applied},{fetch_max_ms:.1f},"
                    f"{apply_ms:.1f},{round_ms:.1f}\n"
                )
                self.metrics_f.flush()
            if reason == "interval":
                with self.round_stats_lock:
                    self.round_ms.append(round_ms)
                    self.round_starts.append(t_round)
                    self.round_rows += rows_applied
                    self.round_failures += len(errors)

            if errors:
                if strict:
                    raise RuntimeError(
                        "barrier round failed: "
                        + "; ".join(f"{p}: {e}" for p, e in errors.items())
                    )
                for p, e in errors.items():
                    # Peers come up in arbitrary order and die first at
                    # teardown. The next round retries a failed pull.
                    log(f"pull from {p} failed: {e}")
            return rows_applied

    def bootstrap_cursors(self, timeout_s=120.0):
        """--assume-converged: this replica began as a converged snapshot of
        the same load as its peers. So everything that a peer holds at startup
        is already present locally. Stamp each cursor at the peer's current
        db_version instead of a re-fetch and re-apply of the entire history.
        That re-apply is idempotent, but it is minutes of work at 1M rows. It
        was about 7% of a 300s run window at 2 writers, and it grows with the
        mesh.

        This runs BEFORE the first round and before ready flips true. A peer
        that never comes up falls back to cursor 0. That is correct either
        way, only slow.
        """
        deadline = time.time() + timeout_s
        pending = set(self.args.peers)
        while pending and not self.stop_event.is_set():
            for peer in sorted(pending):
                try:
                    with urlopen(
                        f"http://{peer}/status", timeout=self.args.http_timeout_s
                    ) as resp:
                        payload = json.loads(resp.read().decode("utf-8"))
                    self.cursors[peer] = payload["db_version"]
                    pending.discard(peer)
                    log(f"bootstrap: cursor[{peer}] = {payload['db_version']}")
                except Exception:
                    pass
            if pending:
                if time.time() > deadline:
                    log(f"bootstrap: giving up on {sorted(pending)}; "
                        "falling back to cursor 0 (full re-pull)")
                    break
                self.stop_event.wait(0.2)
        self.ready = True

    def pull_loop(self):
        if self.args.assume_converged:
            self.bootstrap_cursors()
        interval = self.args.interval_ms / 1000.0
        while not self.stop_event.is_set():
            start = time.time()
            self.round_once("interval", strict=False)
            elapsed = time.time() - start
            self.stop_event.wait(max(0.0, interval - elapsed))

    def shutdown(self):
        for conn in (self.read_conn, self.write_conn):
            try:
                conn.execute("SELECT crsql_finalize()")
                conn.close()
            except Exception:
                pass
        if self.metrics_f:
            self.metrics_f.close()


def make_handler(daemon):
    class Handler(BaseHTTPRequestHandler):
        # Keep-alive, so that a fenced-read client does not pay a TCP
        # handshake per barrier, and does not burn an ephemeral port per
        # barrier. This is safe because every response carries Content-Length.
        protocol_version = "HTTP/1.1"
        # TCP_NODELAY, or every keep-alive response stalls about 40ms. The
        # headers and the body go out as two small segments. Nagle holds the
        # body until the first segment is ACKed. Once the connection leaves
        # Linux's initial quick-ack mode, that ACK is delayed. Measured:
        # 43ms/barrier on the LAN with Nagle, about 1.5ms without.
        # Fresh-connection pulls (/changes) avoid it only because they never
        # leave quick-ack mode.
        disable_nagle_algorithm = True

        def do_GET(self):
            parsed = urlparse(self.path)
            try:
                result = daemon.handle(parsed.path, parse_qs(parsed.query))
            except Exception as e:
                self.send_error(500, str(e))
                return
            if result is None:
                self.send_error(404)
                return
            body = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *log_args):  # quiet
            pass

    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", required=True, help="replica database file")
    p.add_argument("--port", type=int, required=True, help="HTTP listen port")
    p.add_argument("--bind", default="0.0.0.0", help="HTTP bind address")
    p.add_argument("--peers", default="",
                   help="comma-separated host:port list of the other replicas' daemons")
    p.add_argument("--interval-ms", type=float, default=100.0)
    p.add_argument("--batch", type=int, default=4096)
    p.add_argument("--extension", default=None,
                   help="crsqlite loadable extension (default $CRSQLITE_HOME/lib/crsqlite)")
    p.add_argument("--busy-timeout-ms", type=int, default=30000)
    p.add_argument("--synchronous", default="FULL",
                   help="PRAGMA synchronous for applied changes (durability parity)")
    p.add_argument("--http-timeout-s", type=float, default=10.0)
    p.add_argument("--metrics", default=None, help="per-pull metrics CSV path")
    p.add_argument("--assume-converged", action="store_true",
                   help="replica starts as a converged snapshot of the same "
                        "load as every peer: stamp cursors at each peer's "
                        "current db_version instead of re-pulling history. "
                        "ONLY safe when no writes happen anywhere before all "
                        "daemons report ready (run phase from cached "
                        "replicas); NEVER for the load phase.")
    args = p.parse_args()

    if not args.extension:
        home = os.environ.get("CRSQLITE_HOME")
        if not home:
            sys.exit("--extension not given and CRSQLITE_HOME is not set")
        args.extension = os.path.join(home, "lib", "crsqlite")
    args.peers = [x.strip() for x in args.peers.split(",") if x.strip()]

    daemon = Daemon(args)
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(daemon))
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    log(
        f"replica={args.db} site={daemon.site_id.hex()[:8]}.. port={args.port} "
        f"peers={args.peers or '(none)'} interval={args.interval_ms}ms"
    )

    def on_signal(signum, frame):
        daemon.stop_event.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    try:
        daemon.pull_loop()
    finally:
        server.shutdown()
        daemon.shutdown()
        log("stopped")


if __name__ == "__main__":
    main()
