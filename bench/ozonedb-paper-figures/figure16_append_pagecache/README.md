# Figure 16: atomic-append throughput vs writer processes (page cache on/off)

Paper: `images/experiment/fig_append_pagecache_merged_ext4.pdf`, `\label{fig:append_pagecache}`, §5.6 Append Performance.

> N processes append 4 KiB records to a single shared file on an ext4 filesystem.
> *Buffered* uses the page cache; `O_DIRECT` bypasses it.

The original script and data are lost. They were made on the CloudLab node on 2026-08-30.
The benchmark here was **rebuilt on 2026-10-20** from that description and the figure.

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper |
| `data/paper_values.csv` | the paper's values, read back from the figure's PDF (within about 1%) |
| `scripts/append_bench.cpp` | the benchmark (C++17). It forks N writers that append to one shared `O_APPEND` file for a fixed duration, and it reports aggregate ops/s. `--verify` re-reads the file to check that no record was torn. `run_sweep.py` compiles it on first use. |
| `scripts/run_sweep.py` | runs {buffered, O_DIRECT} x {fsync, no-sync} x N with repeats. It writes `runs.csv` and `summary.csv`. |
| `scripts/run_on_cloudlab.sh` | one-command wrapper for the c6525-25g node |
| `scripts/latency_probe.cpp`, `scripts/sanity_checks.sh` | sanity checks: per-operation latencies, journal stats and I/O sampling (see below) |
| `scripts/plot_append_pagecache.py` | plots in the paper's style. `--compare` overlays another summary. |

Paper values (ops/s):

| N | 1 | 2 | 4 | 8 | 16 | 32 |
|---|---|---|---|---|---|---|
| fsync · Buffered | 3,790 | 5,051 | 8,204 | 14,475 | 23,599 | 25,334 |
| fsync · O_DIRECT | 3,811 | 4,929 | 7,001 | 7,019 | 7,212 | 6,963 |
| no-sync · Buffered | 37,290 | 54,606 | 65,219 | 64,460 | 64,241 | 56,203 |
| no-sync · O_DIRECT | 14,151 | 15,928 | 15,743 | 15,285 | 15,101 | 14,446 |

## Rerun the experiment (CloudLab c6525-25g, ext4 on a SATA SSD)

```bash
# On your laptop, copy this folder to the node.
scp -r figure16_append_pagecache <node>:~/

# On the node:
cd ~/figure16_append_pagecache/scripts
lsblk -d -o NAME,ROTA,SIZE,MODEL          # find a spare SATA SSD (ROTA=0)
# If the disk is not mounted as ext4 yet, run the next line. WARNING: it ERASES that disk.
#   sudo mkfs.ext4 -F /dev/sdX && sudo mkdir -p /mnt/ssd && sudo mount /dev/sdX /mnt/ssd && sudo chown $USER /mnt/ssd
bash run_on_cloudlab.sh /mnt/ssd/appendtest
```

The sweep takes about 15 minutes. It makes 72 runs of 10 s each (3 repeats).
The script drops the page cache with `sudo` before every run. If the directory
is not on ext4, the script refuses to run. Results go to
`data/cloudlab_<host>_<time>/`. That directory holds `runs.csv`, `summary.csv`
and `host.txt` with the machine and disk.

## Plot

```bash
cd scripts
python3 plot_append_pagecache.py ../data/<run>/summary.csv --out ../fig_append_pagecache.pdf
# compare against the paper:
python3 plot_append_pagecache.py ../data/<run>/summary.csv \
    --compare ../data/paper_values.csv --compare-label paper --out ../compare.pdf
```

The script needs matplotlib. If you give it `data/paper_values.csv`, it redraws the paper's figure.

## Rebuild run: `data/cloudlab_node0_20261020_1608/`

CloudLab c6525-25g, Linux 5.15, ext4 on `/dev/sdb` (SATA SSD). The C++
benchmark ran with 3 repetitions × 10 s per point. All 72 runs passed
`--verify`.

| File | What it is |
|---|---|
| `summary.csv`, `runs.csv`, `host.txt` | the results, per-run rows, and the machine and disk |
| `fig_append_pagecache.pdf` / `.png` | the figure from this run |
| `compare_with_paper.pdf` | this run in orange, the paper in gray |

| N | 1 | 2 | 4 | 8 | 16 | 32 |
|---|---|---|---|---|---|---|
| fsync · Buffered | 5,406 | 7,414 | 10,274 | 18,295 | 20,819 | 20,011 |
| fsync · O_DIRECT | 5,431 | 6,272 | 7,326 | 7,225 | 7,195 | 6,835 |
| no-sync · Buffered | 211,856 | 156,917 | 151,299 | 149,956 | 152,502 | 127,904 |
| no-sync · O_DIRECT | 22,863 | 21,759 | 21,669 | 21,541 | 20,275 | 19,216 |

**Matches the paper:**
- **fsync:** buffered ≈ `O_DIRECT` at N ≤ 4 (1.0–1.4×). Then buffered scales to 2.9× at N=32 (paper: 3.6×).

**Doesn't match:**
- **no-sync:** buffered is 6.7–9.3× faster than `O_DIRECT` (paper: 2.6–4.3×). The cause is that buffered appends reach 128–212k ops/s, against the paper's 37–65k.
- Earlier checks and the sanity checks below rule out three causes: interpreter overhead, dirty-page throttling and hardware limits. The original setup probably differed in a way that §5.6 does not describe.

## Sanity checks: `data/sanity_node0_20261021_1031/`

These checks find what limits each curve. They ran on the same node and disk
as the rebuild run. `scripts/sanity_checks.sh <dir>` produced them in about
6 minutes. The script:
- runs `scripts/latency_probe.cpp` for single-writer per-operation latencies
- records the disk, kernel and dirty-page settings
- repeats 7 `append_bench` runs, and samples ext4 journal stats, dirty pages and device writes during them

| File | What it is |
|---|---|
| `system.txt` | kernel, mount, disk model, write-cache mode, dirty-page limits |
| `latency.csv` | per-operation latency (mean, p50, p99 in µs) for each probe |
| `jbd2_<run>_{before,after}.txt` | ext4 journal stats around each run |
| `io_<run>.csv` | 0.5 s samples of dirty pages, pages under writeback and MB written to the device |
| `runs.csv` | the `append_bench` results those stats belong to |

**1. There is no device cache flush on this node.**
- The SSD's write cache is off (`write through`, `hdparm -W` = 0, no FUA). The PM883 has power-loss protection. So a write is durable when it completes.
- `fdatasync` with nothing new to persist takes 1.0 µs.
- So the cost of `fsync` is the ext4 journal-commit writes, not a cache flush. The cost is about 135 µs. That is about 3–4 serialized 4 KiB device writes of about 35–42 µs each, and about 6 logged blocks per commit.

**2. Per-operation latency predicts each curve.**

| Curve | Measured cost per operation | Predicted | Sweep |
|---|---|---|---|
| fsync · Buffered, N=1 | write 8.7 µs + fsync 180 µs = 188.8 µs | 5.30k/s | 5.24k |
| fsync · O_DIRECT, N=1 | write 48 µs + fsync 135 µs = 182.6 µs | 5.48k/s | 5.43k |
| no-sync · O_DIRECT | direct 4 KiB append 42.1 µs (overwrite: 34.5 µs) | 23.8k/s, flat in N | 24.0k (N=1), 19.4k (N=32) |

**3. Group commit, counted from the journal.**

| Run | Appends | Journal commits | Appends per commit |
|---|---|---|---|
| fsync · Buffered, N=1 | 52,372 | 52,373 | 1.0 |
| fsync · Buffered, N=32 | 195,898 | 64,403 | 3.04 |
| fsync · O_DIRECT, N=32 | 69,129 | 68,950 | 1.0 |

Buffered fsync scales because each commit carries the appends of about 3
writers at N=32. The math is 6,440 commits/s × 3.04 ≈ 19.6k/s. `O_DIRECT`
gets one commit per append. So it stays flat.

**4. No-sync buffered: throttling exists but does not explain the paper.**
- In a 120 s run, dirty pages climbed for about 70 s to the 20% limit (≈ 21.4 GB of 128 GB). Then they held there.
- After that, the SSD wrote back at a steady ~500 MB/s. Throttling held the writers to that rate: ≈ 122k appends/s, against ≈ 208k/s before the limit. The 10 s sweep points never reach the limit.
- Even with throttling, that rate is about 2× the paper's 37–65k/s (150–265 MB/s). So throttling, the interpreter and the hardware measured here do not explain the paper's no-sync buffered numbers.

## Choices the paper doesn't state

The paper does not give these values, so the rebuild picks them. If you know
the originals, change them in `run_on_cloudlab.sh`:
- **Run length:** 10 s per point.
- **Repeats:** 3, plotted as the mean.
- **Page cache:** dropped before each run.
- **File:** a fresh, empty file for each run.
- **Durability:** `fsync` after every append.
- **O_DIRECT:** set with `open()` on every writer's descriptor.

## Not on macOS

The benchmark builds and runs on a Mac, and its correctness check passes.
But the numbers are not comparable. macOS has no ext4 or `O_DIRECT`
(`F_NOCACHE` stands in). Its `fsync` does not flush the drive's cache.
