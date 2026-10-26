# Figure 16: atomic-append throughput vs writer processes (page cache on/off)

Paper: `images/experiment/fig_append_pagecache_merged_ext4.pdf`, `\label{fig:append_pagecache}`, §5.6 Append Performance.

> N processes append 4 KiB records to a single shared file on an ext4 filesystem.
> *Buffered* uses the page cache; `O_DIRECT` bypasses it.

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

## Run: `data/cloudlab_node0_20261020_1608/`

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

## Sanity checks: `data/sanity_node0_20261021_1031/`

`scripts/sanity_checks.sh <dir>` produced this directory on the same node and disk
as the run above, in about 6 minutes. The script:
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

## Parameters

The paper does not give these values. `run_on_cloudlab.sh` sets them:
- **Run length:** 10 s per point.
- **Repeats:** 3, plotted as the mean.
- **Page cache:** dropped before each run.
- **File:** a fresh, empty file for each run.
- **Durability:** `fsync` after every append.
- **O_DIRECT:** set with `open()` on every writer's descriptor.
