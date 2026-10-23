# Figure 19a: local single writer (YCSB A-F)

Paper: `images/experiment/fig1_single_writer.pdf`, `\label{fig:local_single_writer}` (§5).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper (identical to `data/fig1-single-writer-local/fig1_single_writer.pdf`) |
| `data/fig1-single-writer-local/results.csv` | run throughput per (system, workload), 1 writer |
| `data/fig1-single-writer-local/load_results.csv`, `load_tmpfs_results.csv` | insert (load) throughput on disk and on tmpfs |
| `data/fig1-single-writer-local/*.log` | raw per-run logs for ozonedb, rocksdb, trunkcpp, bcw2, hctree |
| `scripts/plot_single_writer.py` | plot script (use `--mode fig1csv`) |
| `scripts/run_fig2_sweep.py` | run-phase orchestrator (used here with `--threads 1`) |
| `scripts/run_fig2_t1_chain.sh` | waits for a running multi-writer sweep to exit, then starts the T = 1 sweep |
| `scripts/run_fig2_load.py`, `run_fig2_load_tmpfs.py` | load-phase orchestrators (disk, tmpfs) |
| `scripts/rebuild_results_csv.py` | rebuild `results.csv` from the logs |

## Reproduce the plot (verified)

```bash
cd scripts
python3 plot_single_writer.py --mode fig1csv \
  --results-dir ../data/fig1-single-writer-local --output ../paper_figure.pdf
```

Needs matplotlib. `fig1csv` is the mode that matches the paper; the other modes
expect `.result` files this folder does not have.

## Regenerate the data (CloudLab node)

```bash
python3 run_fig2_load.py                    # load (insert) numbers on disk
python3 run_fig2_load_tmpfs.py              # load numbers on tmpfs
python3 run_fig2_sweep.py --threads 1 \
  --results-dir /users/Xinying/ozonedb/bench/results/local/fig1-single-writer-local
python3 rebuild_results_csv.py              # rebuild results.csv if the sweep ran in pieces
```

The scripts use `/users/Xinying/YCSB-cpp` (commit `8401bde`),
`/users/Xinying/rocksdb-9.6.1` and `/users/Xinying/ozonedb`.
