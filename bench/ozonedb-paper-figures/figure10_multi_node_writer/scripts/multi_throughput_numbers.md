# Numbers behind multi_throughput.pdf

The numbers come from the same readers as `plot_throughput_writers.py`.

Four of the five series share one method: a 100 GB dataset, 300 s cells, and
the steady-state throughput. The steady-state throughput is the mean over the
last 120 s of each writer, summed across writers.
`scripts/extract_steady_throughput.py` builds both TSVs.

| series | source | trials | failed ops |
|---|---|---|---|
| OzoneKV, Cassandra-serial | `data/strict100/steady_all.tsv` | 3 in all 50 cells | 0 |
| CR-SQLite sync, async | `data/crsqlite-300g/steady_all.tsv` | 3 in 45 cells, 2 in 5 | 0 |
| SlateDB durable | 1 GB whole-run campaign | 3 | - |

The 5 two-trial cells are all at 16 writers: async on workloads c, d, f, and
sync on d and f.

**SlateDB is the one series still on 1 GB.** Its durable multi-writer path
collapses, so it was never re-run at 100 GB. State that in the caption.

Error bars are the **min-max range across trials**, not a standard deviation.
A standard deviation says nothing on a 2-trial cell. One error-bar meaning
across the figure is worth more than a tighter interval elsewhere.

## Mean throughput (ops/sec)

| series | wl | w2 | w4 | w8 | w16 | w32 |
|---|---|---|---|---|---|---|
| OzoneKV (linearizable) | a | 505 | 1,693 | 2,854 | 5,118 | 7,660 |
| OzoneKV (linearizable) | b | 1,035 | 2,790 | 4,572 | 7,458 | 11,813 |
| OzoneKV (linearizable) | c | 1,268 | 3,486 | 6,703 | 12,808 | 21,091 |
| OzoneKV (linearizable) | d | 2,355 | 4,150 | 6,424 | 9,945 | 15,555 |
| OzoneKV (linearizable) | f | 782 | 1,314 | 2,095 | 3,568 | 6,649 |
| Cassandra-serial | a | 3,145 | 6,097 | 10,663 | 17,108 | 24,798 |
| Cassandra-serial | b | 3,547 | 7,254 | 12,851 | 21,317 | 32,651 |
| Cassandra-serial | c | 3,770 | 7,574 | 13,588 | 22,936 | 35,515 |
| Cassandra-serial | d | 4,809 | 10,036 | 19,497 | 35,550 | 54,120 |
| Cassandra-serial | f | 2,446 | 4,913 | 8,310 | 13,344 | 19,527 |
| CR-SQLite-sync | a | 662 | 648 | 568 | 341 | 182 |
| CR-SQLite-sync | b | 1,324 | 1,093 | 973 | 651 | 331 |
| CR-SQLite-sync | c | 1,448 | 1,189 | 1,069 | 760 | 456 |
| CR-SQLite-sync | d | 1,489 | 1,207 | 1,050 | 718 | 402 |
| CR-SQLite-sync | f | 621 | 517 | 436 | 261 | 145 |
| CR-SQLite-async-10ms | a | 2,167 | 2,327 | 2,406 | 2,141 | 2,508 |
| CR-SQLite-async-10ms | b | 11,220 | 12,391 | 12,159 | 9,182 | 9,047 |
| CR-SQLite-async-10ms | c | 25,219 | 39,261 | 71,848 | 130,583 | 228,352 |
| CR-SQLite-async-10ms | d | 20,647 | 23,870 | 19,411 | 15,762 | 15,896 |
| CR-SQLite-async-10ms | f | 2,128 | 2,265 | 2,396 | 2,255 | 2,510 |
| SlateDB durable (1 GB) | a | 16.7 | 1.9 | 0.1 | 0.0 | 0.0 |
| SlateDB durable (1 GB) | b | 30.6 | 5.8 | 0.9 | 0.1 | 0.1 |
| SlateDB durable (1 GB) | c | 388 | 10.7 | 5.8 | 0.3 | 0.0 |
| SlateDB durable (1 GB) | d | 50.9 | 5.6 | 1.2 | 0.1 | 0.0 |
| SlateDB durable (1 GB) | f | 24.1 | 2.0 | 0.2 | 0.0 | 0.0 |

## Why CR-SQLite-sync falls as writers increase

The decline is real. Each writer is one closed-loop thread, so the fleet rate
is `writers / mean operation latency`. That identity holds to better than
0.5 % at both ends of the sweep (workload a, trial 1):

| writers | mean latency | predicted | measured |
|---|---|---|---|
| 2 | 2.65 ms | 754 ops/s | 753 ops/s |
| 32 | 181 ms | 177 ops/s | 180 ops/s |

Latency is what scales, not throughput. READ latency runs:

- 2,109 us at 2 writers
- 7,055 at 4
- 13,850 at 8
- 46,592 at 16
- 179,449 at 32

That is 85x the latency for 16x the writers, so the fleet rate falls.

A synchronous read waits on every peer. So the work per operation grows with
the peer count, and the system-wide cost grows with its square. The async mode
does not pay this cost. That is why it stays flat instead of falling.

## Two measurement issues found while checking this

1. **YCSB cleanup sample, fixed.** The client prints one last status line a
    few milliseconds after the final full second. That line repeats the second:
    `300 sec: 20465 operations; 333.33 current ops/sec ... [CLEANUP: Count=1]`.
    That rate is one operation over a 3 ms interval. The original extractor
    dropped trailing zero samples but not this one. That overstated
    CR-SQLite-sync by 1.1 % on average and 6.5 % at worst. OzoneKV and
    Cassandra were unaffected, because a fast client fills its last second. So
    the bias was asymmetric across systems. `scripts/extract_steady_throughput.py`
    now drops any trailing sample that repeats the previous elapsed second.
    **The upstream copy in `ozonedb/bench/scripts/` needs the same fix.**
2. **CR-SQLite-sync at 4 writers had not reached steady state, not fixed.**
    Its fleet rate climbs through the whole run: 162, 245, 286, 275, 296, 411,
    582, 619, 651, 657 ops/s in 30 s buckets. A 300 s cell is too short there.
    The plotted value takes the last 120 s. That is the most favourable window
    for CR-SQLite, so the OzoneKV advantage at 4 writers is a lower bound.
    Other writer counts are flat after the first bucket.

## Speedups, all at 100 GB

OzoneKV over CR-SQLite-sync (both linearizable):

| writers | 2 | 4 | 8 | 16 | 32 |
|---|---|---|---|---|---|
| all workloads | 0.76-1.58x | 2.5-3.4x | 4.7-6.3x | 11.5-16.8x | 35.7-46.3x |
| write-heavy (a, f) | 0.76-1.26x | 2.5-2.6x | 4.8-5.0x | 13.7-15.0x | 42.0-45.9x |

OzoneKV over CR-SQLite-async-10ms, which is **not** linearizable:

| writers | 2 | 4 | 8 | 16 | 32 |
|---|---|---|---|---|---|
| all workloads | 0.05-0.37x | 0.09-0.73x | 0.09-1.19x | 0.10-2.39x | 0.09-3.05x |
| write-heavy (a, f) | 0.23-0.37x | 0.58-0.73x | 0.87-1.19x | 1.58-2.39x | 2.65-3.05x |

This is the largest change from the old 1 GB comparison. At 1 GB the async
mode served reads from a local replica that fit in memory, and it won everywhere.
At 100 GB it wins only on workload c, the read-only workload (0.09x). OzoneKV
overtakes it on write-heavy workloads from 8 writers up.

Cassandra-serial over OzoneKV:

| writers | 2 | 4 | 8 | 16 | 32 |
|---|---|---|---|---|---|
| Cassandra ahead by | 2.0-6.2x | 2.2-3.7x | 2.0-4.0x | 1.8-3.7x | 1.7-3.5x |

Workload c converges fastest: 2.97x at 2 writers to 1.68x at 32.

## Worst trial spread (max minus min, as a percent of the mean)

| series | worst | where | 2-trial cells |
|---|---|---|---|
| OzoneKV | 54.7 % | wl b, 2 writers | 0 |
| Cassandra-serial | 17.9 % | wl c, 2 writers | 0 |
| CR-SQLite-sync | 29.0 % | wl a, 2 writers | 2 |
| CR-SQLite-async-10ms | 12.0 % | wl d, 32 writers | 3 |

Spread concentrates at 2 writers, where a fixed startup cost is the largest
share of the run. Cells at 8 writers and above stay in single digits.

## Caveats to keep in the caption

1. Cassandra ran warm: 85 GB of the 103 GB corpus sat in the page cache.
    OzoneKV joined cold (LRU 3.8 %, disk tier 20.7 %, 0 evictions). The
    Cassandra advantage is an upper bound. The OzoneKV rate is a lower bound.
2. CR-SQLite-async-10ms allows 10 ms of staleness. CR-SQLite-sync is the only
    CR-SQLite mode comparable to OzoneKV.
3. SlateDB records 63 fence events at 2 writers and reaches 0.00 ops/sec at
    32 writers. So a ratio against it is not informative above 2 writers. Its
    bars disappear where the rate rounds to zero.

## Cost projection (source: cost2-serial/bench/RESULTS-cost.md)

The table gives USD per month at 10,000 ops/s, RF=3, priced against
`cassandra-serial`. Serial and quorum give the same dollar lines, so the
consistency mode changes the throughput comparison only.

At 10 % reads, the mix of the paper cost figure (`fig_cost`, line 263-266):

| dataset | OzoneKV | Cassandra EBS | ratio |
|---|---|---|---|
| 1 TB | $1,732 | $2,402 | 1.4x |
| 10 TB | $2,073 | $4,742 | 2.3x |
| 100 TB | $4,646 | $45,302 | 9.8x |

OzoneKV is cheaper than the cheapest Cassandra layout from 1.3 TB upward.

At 50 % reads (line 289-294, 305-309), OzoneKV is 5.6x cheaper at 100 TB
($8,112 against $45,302). But Cassandra is 1.7x to 1.9x cheaper in the 100 GB
to 2 TB band.

Do not quote "$5,992 at 10 TB" or "crossover 14.7 TB". Both are superseded.
The current 10 TB values are $2,073 at 10 % reads and $5,097 at 50 %. The
source calls 14.7 TB a grid artefact and asks for a 10 TB to 13 TB band.

Caveat: the campaign measured no cell below 50 % reads, so the 10 % line is an
extrapolation. The Cassandra line is also optimistic, because the model uses
the 10 GB ops-per-node value at every size.

## Scaling from 2 to 32 writers (16x more writers)

Percent is the factor as a share of linear (16x).

| workload | mix | OzoneKV | Cassandra-serial | CR-SQLite-async | CR-SQLite-sync |
|---|---|---|---|---|---|
| a | 50 % read / 50 % update | 15.2x (95 %) | 7.9x | 1.16x | 0.28x |
| b | 95 % read / 5 % update | 11.4x (71 %) | 9.2x | 0.81x | 0.25x |
| c | 100 % read | 16.6x (104 %) | 9.4x | 9.05x (57 %) | 0.31x |
| d | 95 % read / 5 % insert | 6.6x (41 %) | 11.3x | 0.77x | 0.27x |
| f | 50 % read / 50 % RMW | 8.5x (53 %) | 8.0x | 1.18x | 0.23x |

OzoneKV ranges 6.6x to 16.6x. Workload c scales best, workload d least.

**Workload d is read-heavy, not write-heavy.** It is 95 % read and 5 % insert
(read-latest). The write-heavy workloads are a (50 % update) and f (50 %
read-modify-write).

### These factors depend on a noisy 2-writer baseline

OzoneKV's 2-writer cells carry the largest trial spread, because a fixed cold
start is the biggest share of a 300 s run there. The best and worst trial at
each end give:

| workload | mean | band across trials | 2-writer trials |
|---|---|---|---|
| a | 15.2x | 13.8x - 17.9x | 438 - 540 |
| b | 11.4x | 9.2x - 16.5x | 718 - 1,284 |
| c | 16.6x | 13.1x - 22.1x | 956 - 1,597 |
| d | 6.6x | 6.2x - 7.3x | 2,134 - 2,493 |
| f | 8.5x | 7.8x - 9.7x | 687 - 846 |

Workloads a, d and f are tight. Workloads b and c are not, so do not quote
16.6x to three significant figures. Cassandra's and CR-SQLite's bands are
tight everywhere.

### The 1 GB numbers these replace

The superseded 1 GB campaign gave OzoneKV-durable 3.95x (wl d) to 9.84x
(wl c). It gave CR-SQLite-async 15.22x in workload c (95 % of linear) and
2.08x in workload a. Any paper text that carries "3.9-9.8x", "9.8x", "3.9x",
"95 % efficiency" or "2.1x" is written against that superseded data.
