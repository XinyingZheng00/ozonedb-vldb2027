"""Background resource sampler for one question: what does the
client-server architecture actually cost? It samples real RSS memory and
CPU usage from the live OS during a trial. It does not estimate them.

Two groups are sampled every `interval_s` while the `Tuner.fit()` of a
trial runs:

  - `postgres` processes (matched by `name() == "postgres"`, real): every
    real `postgres` OS process on this host. This includes the background
    processes (checkpointer, walwriter, autovacuum launcher, stats
    collector, ...) and the per-connection backends. This is the true
    total cost of a Postgres server that is available to serve this
    workload, including its fixed idle overhead. It is only meaningful
    (and only sampled) for the `postgres` and `postgres_serialized` arms.
    For `sqlite` and `ozonedb` this is exactly zero by construction. No
    such process exists at all, it is not only "small". So the sampler
    does not sample it for those arms. Otherwise it would report a
    misleading nonzero number from the *other*, unrelated idle presence
    of Postgres on this shared host.
  - the own process tree of the driver (`psutil.Process().children(recursive=True)`):
    the own infrastructure of Ray (raylet, GCS, dashboard/runtime-env
    agents) plus every `train_and_log` trial worker. This is the real
    training compute common to all four backends. The sampler samples it
    for all of them, to check (not assume) that it is actually similar
    across backends.

Peak RSS (not mean) is reported for both groups. To provision for a
workload means to provision for its peak, not its average. Mean CPU% is
reported, because that is the steady-state cost that matters for a
shared host.
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
        # cpu_percent(interval=None) reports the delta since the previous call
        # on that *same* Process object. A freshly constructed Process always
        # reports 0.0 on its first call. Cache by pid, so repeated ticks reuse
        # the same object and get real deltas.
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
        # Prime cpu_percent(). Its first call per process returns the average
        # since process start, not since the last call. Discard that sample.
        # Otherwise a stale pre-trial average would pollute peak and mean.
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
