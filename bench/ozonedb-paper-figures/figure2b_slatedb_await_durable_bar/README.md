# Figure 2b: SlateDB multi-writer throughput (await-durable)

Paper: `images/experiment/single-writer-await-durable-5ms-100ms-m6id-2xlarge-bar.pdf`,
`\label{fig:second_generation_dis}` (§2 Motivation). Caption: "SlateDB Multi-Writer
Throughput."

| File | What it is |
| --- | --- |
| `paper_figure.pdf` | the figure as it appears in the paper |
| `data/single-writer-await-durable-5ms-100ms-m6id-2xlarge.csv` | one row per run: 1 to 32 writers × flush intervals 5 to 100 ms × 10 trials (360 rows), `--await-durable`, AWS m6id.2xlarge. The plot uses `effective_puts_per_s`. The last three columns (`total_gets`, `total_fences`, `fenced_before_flush`) are invalid: the sweep script that wrote this file used `\10`, `\11`, `\12` in a sed replacement, which sed reads as `\1` plus a digit. The script is fixed; the archived rows keep the wrong values because the run logs were not kept |
| `scripts/plot-multi-writer-bar.py` | the plot script. It draws grouped bars by writer count and flush interval. Each bar is the mean ± standard error over the trials |
| `scripts/benchmark-multiwriter.sh` | the sweep: 10 trials × writers 1, 2, 4, 8, 16, 32 × flush intervals 5, 10, 20, 40, 60, 100 ms, 60 s per run, `--reopen-on-fence`. It writes `target/bencher/results/results.csv` |
| `scripts/SlateDb.toml`, `scripts/src/multi_writer.rs`, `scripts/src/args.rs` | the bencher's config and its `multi-writer` subcommand (crate `slatedb-bencher` in the fork) |

## Reproduce the plot

```bash
cd scripts
python3 plot-multi-writer-bar.py ../data/single-writer-await-durable-5ms-100ms-m6id-2xlarge.csv \
  --out ../single-writer-await-durable-5ms-100ms-m6id-2xlarge-bar.pdf
```

The script needs matplotlib, pandas and numpy. `uv run --script plot-multi-writer-bar.py ...`
installs the versions that the script header pins. Without `--out`, the script writes
`<csv>-bar.pdf` and `.png` next to the CSV.

## Regenerate the data

Use the SlateDB fork `github.com/oliverrogalski1001/slatedb`, branch `ozonedb-benchmark`
(`cfebf81`). Export `SLATEDB_BENCH_AWAIT_DURABLE=1`. Then run `benchmark-multiwriter.sh`
from `slatedb-bencher/`. The run takes about 1 h. The script needs an S3 compatible object
storage.
