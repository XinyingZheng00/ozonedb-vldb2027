# Figure 19a: local single writer (YCSB A-F)

Paper: `images/experiment/fig1_single_writer.pdf`, `\label{fig:local_single_writer}` (§5).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper. It is identical to `data/fig1-single-writer-local/fig1_single_writer.pdf` |
| `data/fig1-single-writer-local/results.csv` | run throughput per (system, workload), 1 writer |
| `data/fig1-single-writer-local/load_results.csv`, `load_tmpfs_results.csv` | insert (load) throughput on disk and on tmpfs |
| `data/fig1-single-writer-local/*.log` | raw per-run logs for ozonedb, rocksdb, trunkcpp, bcw2, hctree |
| `scripts/plot_single_writer.py` | the plot script. Use `--mode fig1csv` |
| `scripts/run_fig2_sweep.py` | the run-phase orchestrator. This figure uses it with `--threads 1` |
| `scripts/run_fig2_t1_chain.sh` | waits for a running multi-writer sweep to exit. Then it starts the T = 1 sweep |
| `scripts/run_fig2_load.py`, `run_fig2_load_tmpfs.py` | load-phase orchestrators (disk, tmpfs) |
| `scripts/rebuild_results_csv.py` | rebuild `results.csv` from the logs |

## Reproduce the plot

```bash
cd scripts
python3 plot_single_writer.py --mode fig1csv \
  --results-dir ../data/fig1-single-writer-local --output ../paper_figure.pdf
```

The script needs matplotlib. The `fig1csv` mode matches the paper. The other
modes expect `.result` files. This folder does not have those files.

## Regenerate the data (CloudLab node)

```bash
python3 run_fig2_load.py                    # load (insert) numbers on disk
python3 run_fig2_load_tmpfs.py              # load numbers on tmpfs
python3 run_fig2_sweep.py --threads 1 \
  --results-dir $OZONEDB_HOME/bench/results/local/fig1-single-writer-local
python3 rebuild_results_csv.py              # rebuild results.csv if the sweep ran in pieces
```

The scripts read the checkouts from `YCSB_CPP_DIR` (commit `8401bde`),
`ROCKSDB_PREFIX` and `OZONEDB_HOME`; the defaults are `~/YCSB-cpp`, `~/rocksdb-9.6.1`
and `~/ozonedb`.
