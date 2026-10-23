# Figure 16: atomic-append throughput vs writer processes (page cache on/off)

Paper: `images/experiment/fig_append_pagecache_merged_ext4.pdf`, `\label{fig:append_pagecache}`, §5.6 Append Performance.

> N processes append 4 KiB records to a single shared file on an ext4 filesystem.
> *Buffered* uses the page cache; `O_DIRECT` bypasses it.

The original script and data were lost (made on the CloudLab node on 2026-08-30).
The benchmark here was **rebuilt on 2026-10-20** from that description and the figure.

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper |
| `data/paper_values.csv` | the paper's values, read back from the figure's PDF (within about 1%) |
| `scripts/append_bench.cpp` | the benchmark (C++17): N forked writers, one shared `O_APPEND` file, fixed duration, aggregate ops/s; `--verify` re-reads the file to check no record was torn. `run_sweep.py` compiles it on first use. |
| `scripts/run_sweep.py` | runs {buffered, O_DIRECT} x {fsync, no-sync} x N with repeats; writes `runs.csv` and `summary.csv` |
| `scripts/run_on_cloudlab.sh` | one-command wrapper for the c6525-25g node |
| `scripts/latency_probe.cpp`, `scripts/sanity_checks.sh` | sanity checks: per-operation latencies, journal stats and I/O sampling (see below) |
| `scripts/plot_append_pagecache.py` | plot in the paper's style; `--compare` overlays another summary |

Paper values (ops/s):

| N | 1 | 2 | 4 | 8 | 16 | 32 |
|---|---|---|---|---|---|---|
| fsync · Buffered | 3,790 | 5,051 | 8,204 | 14,475 | 23,599 | 25,334 |
| fsync · O_DIRECT | 3,811 | 4,929 | 7,001 | 7,019 | 7,212 | 6,963 |
| no-sync · Buffered | 37,290 | 54,606 | 65,219 | 64,460 | 64,241 | 56,203 |
| no-sync · O_DIRECT | 14,151 | 15,928 | 15,743 | 15,285 | 15,101 | 14,446 |

## Rerun the experiment (CloudLab c6525-25g, ext4 on a SATA SSD)

```bash
# on your laptop: copy this folder to the node
scp -r figure16_append_pagecache <node>:~/

# on the node
cd ~/figure16_append_pagecache/scripts
lsblk -d -o NAME,ROTA,SIZE,MODEL          # find a spare SATA SSD (ROTA=0)
# if it isn't mounted as ext4 yet (this ERASES that disk):
#   sudo mkfs.ext4 -F /dev/sdX && sudo mkdir -p /mnt/ssd && sudo mount /dev/sdX /mnt/ssd && sudo chown $USER /mnt/ssd
bash run_on_cloudlab.sh /mnt/ssd/appendtest
```

This takes about 15 minutes: 72 runs of 10 s each (3 repeats). The page cache
is dropped before every run with `sudo`. The script refuses to run if the
directory isn't on ext4. Results land in `data/cloudlab_<host>_<time>/`
(`runs.csv`, `summary.csv`, `host.txt` with the machine and disk).

## Plot

```bash
cd scripts
python3 plot_append_pagecache.py ../data/<run>/summary.csv --out ../fig_append_pagecache.pdf
# compare against the paper:
python3 plot_append_pagecache.py ../data/<run>/summary.csv \
    --compare ../data/paper_values.csv --compare-label paper --out ../compare.pdf
```

Needs matplotlib. Fed `data/paper_values.csv`, the script redraws the paper's figure.

## Rebuild run: `data/cloudlab_node0_20261020_1608/`

CloudLab c6525-25g, Linux 5.15, ext4 on `/dev/sdb` (SATA SSD). The C++
benchmark ran with 3 repetitions × 10 s per point, and all 72 runs passed
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
- **fsync:** buffered ≈ `O_DIRECT` at N ≤ 4 (1.0–1.4×), then buffered scales to 2.9× at N=32 (paper: 3.6×).

**Doesn't match:**
- **no-sync:** buffered is 6.7–9.3× faster than `O_DIRECT` (paper: 2.6–4.3×), because buffered appends reach 128–212k ops/s against the paper's 37–65k.
- Earlier checks and the sanity checks below rule out interpreter overhead, dirty-page throttling and hardware limits as the cause. The original likely differed in a way §5.6 doesn't describe.

## Sanity checks: `data/sanity_node0_20261021_1031/`

These check what limits each curve, measured on the same node and disk as the
rebuild run. They were produced by `scripts/sanity_checks.sh <dir>` (about
6 minutes), which:
- runs `scripts/latency_probe.cpp` for single-writer per-operation latencies
- records the disk, kernel and dirty-page settings
- repeats 7 `append_bench` runs while sampling ext4 journal stats, dirty pages and device writes

| File | What it is |
|---|---|
| `system.txt` | kernel, mount, disk model, write-cache mode, dirty-page limits |
| `latency.csv` | per-operation latency (mean, p50, p99 in µs) for each probe |
| `jbd2_<run>_{before,after}.txt` | ext4 journal stats around each run |
| `io_<run>.csv` | 0.5 s samples of dirty pages, pages under writeback and MB written to the device |
| `runs.csv` | the `append_bench` results those stats belong to |

**1. There's no device cache flush on this node.**
- The SSD's write cache is off (`write through`, `hdparm -W` = 0, no FUA). The PM883 has power-loss protection, so a write is durable when it completes.
- `fdatasync` with nothing new to persist takes 1.0 µs.
- So `fsync` costs ext4 journal-commit writes, not a cache flush: about 135 µs ≈ 3–4 serialized 4 KiB device writes of about 35–42 µs each, and about 6 logged blocks per commit.

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

Buffered fsync scales because each commit carries about 3 writers' appends at
N=32: 6,440 commits/s × 3.04 ≈ 19.6k/s. `O_DIRECT` gets one commit per
append, so it stays flat.

**4. No-sync buffered: throttling exists but doesn't explain the paper.**
- In a 120 s run, dirty pages climbed for about 70 s to the 20% limit (≈ 21.4 GB of 128 GB), then held.
- After that the SSD wrote back at a steady ~500 MB/s, and writers were held to it: ≈ 122k appends/s, against ≈ 208k/s before the limit. 10 s sweep points never reach the limit.
- Even throttled, that's about 2× the paper's 37–65k/s (150–265 MB/s). So the paper's no-sync buffered numbers aren't explained by throttling, the interpreter or the hardware measured here.

## Choices the paper doesn't state

The paper doesn't give these, so the rebuild picks them; change them in
`run_on_cloudlab.sh` if you know the originals:
- **Run length:** 10 s per point.
- **Repeats:** 3, plotted as the mean.
- **Page cache:** dropped before each run.
- **File:** a fresh, empty file for each run.
- **Durability:** `fsync` after every append.
- **O_DIRECT:** set with `open()` on every writer's descriptor.

## Not on macOS

The benchmark builds and runs on a Mac, and its correctness check passes.
The numbers aren't comparable, though: macOS has no ext4 or `O_DIRECT`
(`F_NOCACHE` stands in), and its `fsync` doesn't flush the drive's cache.
