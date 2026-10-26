"""Background resource sampler for the "what does the client-server
architecture actually cost" question: real RSS memory and CPU usage,
sampled from the live OS during a trial, not estimated.

Two groups, sampled every `interval_s` while a trial's `Tuner.fit()` runs:

  - `postgres` processes (matched by `name() == "postgres"`, real): every
    real `postgres` OS process on this host, background (checkpointer,
    walwriter, autovacuum launcher, stats collector, ...) and per-
    connection backends alike -- the true total cost of having a Postgres
    server available to serve this workload, including its fixed idle
    overhead. Only meaningful (and only sampled) for the `postgres`/
    `postgres_serialized` arms; for `sqlite`/`ozonedb` this is exactly
    zero by construction -- no such process exists at all, not just
    "small" -- so it's not sampled for those arms rather than reporting a
    misleading nonzero number from Postgres's *other*, unrelated idle
    presence on this shared host.
  - the driver's own process tree (`psutil.Process().children(recursive=True)`):
    Ray's own infrastructure (raylet, GCS, dashboard/runtime-env agents)
    plus every `train_and_log` trial worker -- the real training compute
    common to all four backends, sampled for all of them to check (not
    assume) whether it's actually similar across backends.

Peak RSS (not mean) is reported for both groups, since provisioning for a
workload means provisioning for its peak, not its average; mean CPU% is
reported since that's the steady-state cost that matters for a shared
host.
"""
import threading
import time

import psutil


class ResourceSampler:
    def __init__(self, interval_s=1.0, sample_postgres=False):
        self.interval_s = interval_s
        self.sample_postgres = sample_postgres
        self._stop = threading.Event()
        self._thread = None
        self.pg_rss_mb_samples = []
        self.pg_cpu_pct_samples = []
        self.client_rss_mb_samples = []
        self.client_cpu_pct_samples = []
        self._driver = psutil.Process()
        # cpu_percent(interval=None) reports the delta since that *same*
        # Process object's previous call -- a freshly-constructed Process
        # always reports 0.0 on its first call. Cache by pid so repeated
        # ticks reuse the same object and get real deltas.
        self._client_proc_cache = {}
        self._pg_proc_cache = {}

    def _tracked_procs(self, live_procs, cache):
        live_pids = {p.pid for p in live_procs}
        for pid in list(cache):
            if pid not in live_pids:
                del cache[pid]
        for proc in live_procs:
            if proc.pid not in cache:
                cache[proc.pid] = proc
                cache[proc.pid].cpu_percent(interval=None)  # prime, discard
        return list(cache.values())

    def _postgres_procs(self):
        out = []
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"] == "postgres":
                    out.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return out

    def _sample_once(self):
        client_procs = self._tracked_procs(self._driver.children(recursive=True),
                                            self._client_proc_cache)
        client_rss = 0.0
        client_cpu = 0.0
        for proc in client_procs:
            try:
                client_rss += proc.memory_info().rss / (1024 * 1024)
                client_cpu += proc.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        self.client_rss_mb_samples.append(client_rss)
        self.client_cpu_pct_samples.append(client_cpu)

        if self.sample_postgres:
            pg_procs = self._tracked_procs(self._postgres_procs(), self._pg_proc_cache)
            pg_rss = 0.0
            pg_cpu = 0.0
            for proc in pg_procs:
                try:
                    pg_rss += proc.memory_info().rss / (1024 * 1024)
                    pg_cpu += proc.cpu_percent(interval=None)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            self.pg_rss_mb_samples.append(pg_rss)
            self.pg_cpu_pct_samples.append(pg_cpu)

    def _run(self):
        # Prime cpu_percent() -- its first call per process returns the
        # average since process start, not since-last-call; discard that
        # sample rather than let a stale pre-trial average pollute peak/mean.
        self._sample_once()
        self.client_rss_mb_samples.clear()
        self.client_cpu_pct_samples.clear()
        self.pg_rss_mb_samples.clear()
        self.pg_cpu_pct_samples.clear()
        while not self._stop.is_set():
            self._sample_once()
            self._stop.wait(self.interval_s)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def summary(self):
        def peak(xs):
            return max(xs) if xs else 0.0

        def mean(xs):
            return sum(xs) / len(xs) if xs else 0.0

        return {
            "client_peak_rss_mb": peak(self.client_rss_mb_samples),
            "client_mean_cpu_pct": mean(self.client_cpu_pct_samples),
            "postgres_peak_rss_mb": peak(self.pg_rss_mb_samples),
            "postgres_mean_cpu_pct": mean(self.pg_cpu_pct_samples),
        }
