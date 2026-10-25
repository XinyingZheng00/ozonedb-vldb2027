# Figure 9: local multi-process writers

Paper: `images/experiment/fig2_multi_process_bars.pdf`, `\label{fig:local_multi_process_writer}` (§5).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper |
| `data/phase1-multi-process/` | the multi-process run. N processes write to one shared DB. Contains `results.csv`, per-run `logs/` and `sweep.log` |
| `data/fig2-multi-writer-local/` | the multi-thread run. Contains `results.csv`, per-run logs and `REPORT.md`. `REPORT.md` holds the full procedure and the configs |
| `data/configs/`, `data/REPORT.md`, `data/WORKLOG.md` | run configuration and notes |
| `data/fig2_multi_process_bars.pdf` | the plot output. It is the same file as `paper_figure.pdf` |
| `scripts/plot_phase1_paper.py` | plot script |
| `scripts/run_phase1_shared_db_sweep.py` | the orchestrator for the multi-process data. Systems: ozonedb, trunkcpp, bcw2. N = 2, 4, 8, 16 |
| `scripts/run_phase1_multiproc_sweep.py` | the independent-DB variant. It also holds the helpers that the shared-DB sweep imports |
| `scripts/run_fig2_sweep.py`, `rebuild_results_csv.py`, `plot_fig2.py` | the multi-thread sweep, the CSV rebuild from the logs, and its plot |

## Reproduce the plot

```bash
cd scripts
python3 plot_phase1_paper.py \
  --thread-csv ../data/fig2-multi-writer-local/results.csv \
  --proc-csv   ../data/phase1-multi-process/results.csv \
  --out-dir    ..
# The script writes ../fig2_multi_process_bars.pdf. That file is the paper figure.
# It also writes multi_thread_lines.pdf and multi_process_lines.pdf. These are not in the paper.
```

The script needs matplotlib and numpy. If you omit the flags, the script uses
the directories under `$OZONEDB_HOME/bench/results/local/` (default `~/ozonedb`).

## Regenerate the data (CloudLab node)

```bash
python3 run_phase1_shared_db_sweep.py --results-dir <out>   # multi-process data
python3 run_fig2_sweep.py                                    # multi-thread data
python3 rebuild_results_csv.py ...                           # if the sweep ran in several chunks
```

The scripts read the checkouts from `YCSB_CPP_DIR` (commit `8401bde`), `OZONEDB_HOME` and
`ROCKSDB_PREFIX`; the defaults are `~/YCSB-cpp`, `~/ozonedb` and `~/rocksdb-9.6.1`.
The `ozonedb` checkout must contain
`bench/scripts/generate_workload.py` and `bench/scripts/local/run_local_ycsb.py`.
For details, see `data/fig2-multi-writer-local/REPORT.md`.
