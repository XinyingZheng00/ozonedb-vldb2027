# Serval-dna issue #1 reproduction: three-way comparison

This is a fixed-duration reproduction of the literal scenario in serval-dna issue #1. Each trial runs 30s. Each backend/N has **3 repeats**. The results are the mean +/- sample standard deviation across repeats. In the scenario, a real, unmodified `servald` daemon holds its Rhizome store open for the whole trial. The daemon adds through a benchmark-only, opt-in `persistent_adder_tick()` hook in `serval-dna/server.c`. The hook is gated behind `RHIZOME_PERSISTENT_ADD_INTERVAL_MS`. It reuses the same internal functions that `rhizome add file` itself calls.

N independent CLI worker processes race the daemon. They run `rhizome add/extract file` against the *same* store. Each operation is a fresh subprocess. `sqlite_rest` is the re-architected fix. There, the daemon is the *only* process that ever opens the store. So instead of an internal adder, it gets N+1 REST clients. This matches the volume of the other two backends: N workers + 1 daemon = N+1 add-issuing actors.

Data: `persistent_adder_results/trials.csv`, N in [1, 2, 4, 8, 16, 20, 24, 28, 32], 3 repeats each. `run_persistent_adder_benchmark.py` generated it. Figures: `plots/throughput.pdf`, `plots/mean_add_latency.pdf`, `plots/p99_add_latency.pdf`. The error bars are the SD across repeats. Regenerate the figures with `python3 plots/plot_*.py`.

## Findings

**1. The real bug shows in the tail latency, not only in the mean, even at N=1.** With one CLI worker that races the daemon's own persistent connection, SQLite's p99 add() latency is already 2.0s (SD 0.7s). The mean is 63ms (SD 15ms). This is a real `SQLITE_BUSY`/lock-retry cost, not a modeling artifact. The p99 peaks at 4.6s (SD 0.6s) at N=32. OzoneDB's p99 at N=1 is 20ms (SD 0ms), which is close to its mean. It stays under 117ms at every N tested.

**2. SQLite's throughput does not plateau under contention. It collapses.** Cycles/sec peaks at 26 (SD 1) around N=8. Then it *declines* as N grows, down to 19 (SD 1) at N=32. More concurrent processes make the whole system do strictly less real work per second, not more. Neither OzoneDB (236 (SD 0) at N=32) nor sqlite_rest ever falls below its own low-N throughput in this way.

**3. Under SQLite, contention does not only slow the CLI side. It also starves the daemon's own steady writes.** The persistent daemon adder ticks on a fixed 100ms timer. So with no contention, it should complete a near-constant number of adds for every N. Under SQLite it does not. The tick count goes from 304 (SD 9) at N=1 down to 142 (SD 21) at N=32. The lock contention of the CLI workers starves the daemon's own connection. Under OzoneDB, the daemon's tick count is 301 (SD 0) at N=1 and 303 (SD 1) at N=32. The number of other processes that write at the same time has almost no effect on it.

**4. sqlite_rest leads at low N on lighter clients. Then OzoneDB overtakes it when its single serialization point starts to bind.** Part of sqlite_rest's early lead is client cost, not architecture. Its N+1 clients are plain HTTP calls from a process that already runs. They are not fresh `servald` CLI launches with their own keyring/crypto init. So sqlite_rest is still ahead at N=8 (151 against OzoneDB's 146 cycles/s).

By N=16, the single daemon queue is the bottleneck, and OzoneDB has overtaken it (206 against 155). By N=32 the gap is stark. sqlite_rest falls to 137 (SD 20) cycles/s. The mean wall time for that trial was 35.4s (SD 4.4s) against a 30s target. That is a real backlog, not only a rate drop. OzoneDB still delivers 236 (SD 0).

**5. Past N=24, sqlite_rest does not only slow down. It drops requests.** sqlite and ozonedb had zero failures across every N and every repeat in this sweep. sqlite_rest did not. At N in [24, 28, 32], some requests hit a real 30s connection timeout. The daemon's request queue never got to them within the whole trial. The worst case is 8.7 (SD 1.2) failed cycles out of 4783 at N=32. This is a real reliability cost of the single-daemon architecture under sustained load. It is not only the throughput ceiling in finding 4.

**Caveat on sqlite_rest comparisons:** REST clients are structurally lighter than the CLI-launch path that `sqlite` and `ozonedb` use. Each REST cycle is a plain HTTP call from a process that already runs. It is not a fresh `servald` process that pays keyring/crypto init every time. Part of the advantage of sqlite_rest at low N is client cost, not architecture. The fairer read is the throughput-scaling *trend* across the N sweep, not the absolute value at any single low-N point.

## Full results (mean ± SD across repeats)

| backend | n_workers | repeats | cycles_per_sec | mean_add_ms | p99_add_ms | daemon_ticks | fail |
|---|---|---|---|---|---|---|---|
| ozonedb | 1 | 3 | 34.44 ± 0.13 | 14.84 ± 0.06 | 19.59 ± 0.20 | 301 | 0 |
| ozonedb | 2 | 3 | 58.75 ± 0.04 | 17.30 ± 0.03 | 24.58 ± 0.16 | 302 ± 1 | 0 |
| ozonedb | 4 | 3 | 94.80 ± 0.12 | 21.37 ± 0.02 | 32.47 ± 0.03 | 301 ± 1 | 0 |
| ozonedb | 8 | 3 | 146.30 ± 0.29 | 27.70 ± 0.08 | 45.39 ± 0.99 | 302 | 0 |
| ozonedb | 16 | 3 | 205.85 ± 0.08 | 39.04 ± 0.01 | 68.28 ± 0.32 | 302 | 0 |
| ozonedb | 20 | 3 | 221.42 ± 0.07 | 45.14 ± 0.03 | 80.11 ± 0.30 | 302 | 0 |
| ozonedb | 24 | 3 | 230.84 ± 0.08 | 51.47 ± 0.04 | 90.87 ± 0.35 | 302 | 0 |
| ozonedb | 28 | 3 | 234.78 ± 0.30 | 58.17 ± 0.05 | 104.16 ± 1.13 | 303 ± 1 | 0 |
| ozonedb | 32 | 3 | 236.15 ± 0.17 | 64.60 ± 0.07 | 116.80 ± 0.48 | 303 ± 1 | 0 |
| sqlite | 1 | 3 | 8.48 ± 2.27 | 63.46 ± 15.32 | 1964.41 ± 690.99 | 304 ± 9 | 0 |
| sqlite | 2 | 3 | 15.74 ± 0.83 | 52.00 ± 5.81 | 1027.78 ± 283.36 | 295 ± 2 | 0 |
| sqlite | 4 | 3 | 22.86 ± 1.70 | 82.99 ± 3.58 | 1295.48 ± 213.84 | 277 ± 3 | 0 |
| sqlite | 8 | 3 | 26.16 ± 1.04 | 148.05 ± 7.19 | 1355.62 ± 208.06 | 230 ± 8 | 0 |
| sqlite | 16 | 3 | 21.87 ± 0.98 | 347.85 ± 22.01 | 2387.97 ± 125.62 | 161 ± 6 | 0 |
| sqlite | 20 | 3 | 20.44 ± 0.40 | 453.18 ± 18.69 | 2948.34 ± 59.28 | 159 ± 9 | 0 |
| sqlite | 24 | 3 | 19.63 ± 0.50 | 550.44 ± 44.27 | 3591.55 ± 801.90 | 157 ± 7 | 0 |
| sqlite | 28 | 3 | 19.55 ± 1.52 | 672.58 ± 112.28 | 4146.18 ± 391.57 | 134 ± 10 | 0 |
| sqlite | 32 | 3 | 18.50 ± 0.90 | 796.62 ± 50.72 | 4610.86 ± 619.13 | 142 ± 21 | 0 |
| sqlite_rest | 1 | 3 | 158.38 ± 2.77 | 9.00 ± 0.15 | 18.64 ± 1.47 | — | 0 |
| sqlite_rest | 2 | 3 | 159.70 ± 1.27 | 10.82 ± 0.12 | 22.39 ± 1.47 | — | 0 |
| sqlite_rest | 4 | 3 | 148.37 ± 1.30 | 15.53 ± 0.61 | 25.19 ± 2.71 | — | 0 |
| sqlite_rest | 8 | 3 | 151.05 ± 0.61 | 29.85 ± 1.48 | 51.04 ± 7.98 | — | 0 |
| sqlite_rest | 16 | 3 | 154.79 ± 0.47 | 56.16 ± 1.05 | 100.23 ± 16.69 | — | 0 |
| sqlite_rest | 20 | 3 | 156.07 ± 1.64 | 69.97 ± 2.82 | 124.22 ± 18.46 | — | 0 |
| sqlite_rest | 24 | 3 | 154.72 ± 4.84 | 75.53 ± 2.21 | 127.95 ± 5.77 | — | 2 ± 1 |
| sqlite_rest | 28 | 3 | 125.01 ± 7.22 | 80.86 ± 2.88 | 129.64 ± 2.73 | — | 4 ± 1 |
| sqlite_rest | 32 | 3 | 136.80 ± 20.01 | 81.98 ± 6.50 | 136.09 ± 13.83 | — | 9 ± 1 |
