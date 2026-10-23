# Figure 9: local multi-process writers

Paper: `images/experiment/fig2_multi_process_bars.pdf`, `\label{fig:local_multi_process_writer}` (§5).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper |
| `data/phase1-multi-process/` | multi-process run (N processes on one shared DB): `results.csv`, per-run `logs/`, `sweep.log` |
| `data/fig2-multi-writer-local/` | multi-thread run: `results.csv`, per-run logs, `REPORT.md` (full procedure and configs) |
| `data/configs/`, `data/REPORT.md`, `data/WORKLOG.md` | run configuration and notes |
| `data/fig2_multi_process_bars.pdf` | plot output (same as `paper_figure.pdf`) |
| `scripts/plot_phase1_paper.py` | plot script |
| `scripts/run_phase1_shared_db_sweep.py` | orchestrator for the multi-process data (systems ozonedb, trunkcpp, bcw2; N = 2, 4, 8, 16) |
| `scripts/run_phase1_multiproc_sweep.py` | independent-DB variant; also holds helpers the shared-DB sweep imports |
| `scripts/run_fig2_sweep.py`, `rebuild_results_csv.py`, `plot_fig2.py` | multi-thread sweep, CSV rebuild from logs, and its plot |

## Reproduce the plot (verified)

```bash
cd scripts
python3 plot_phase1_paper.py \
  --thread-csv ../data/fig2-multi-writer-local/results.csv \
  --proc-csv   ../data/phase1-multi-process/results.csv \
  --out-dir    ..
# writes ../fig2_multi_process_bars.pdf (the paper figure),
# plus multi_thread_lines.pdf and multi_process_lines.pdf (not in the paper)
```

Needs matplotlib and numpy. Without the flags the script uses CloudLab paths
under `/users/Xinying/ozonedb/...`.

## Regenerate the data (CloudLab node)

```bash
python3 run_phase1_shared_db_sweep.py --results-dir <out>   # multi-process data
python3 run_fig2_sweep.py                                    # multi-thread data
python3 rebuild_results_csv.py ...                           # if the sweep ran in several chunks
```

They expect `/users/Xinying/YCSB-cpp` (commit `8401bde`), `/users/Xinying/ozonedb`
(with `bench/scripts/generate_workload.py` and `bench/scripts/local/run_local_ycsb.py`)
and `/users/Xinying/rocksdb-9.6.1`. Details: `data/fig2-multi-writer-local/REPORT.md`.
