# Figure 19a: local single writer (YCSB A-F)

Paper: `images/experiment/fig1_single_writer.pdf`, `\label{fig:local_single_writer}` (§5).

```
run_fig19a.sh                                  run the experiment and draw the plot
scripts/run_load.py                            load 1M x 1 KB records for the 5 systems on /tank
scripts/run_load_tmpfs.py                      the same load on tmpfs (/dev/shm)
scripts/run_sweep.py                           YCSB run phase (here with 1 writer)
scripts/plot_single_writer.py                  plot script (use --mode fig1csv)
scripts/rebuild_results_csv.py                 rebuild results.csv from per-run logs, if a sweep ran in chunks
data/paper_data/fig1-single-writer-local/      the paper's data: results.csv, load_results.csv, load_tmpfs_results.csv, logs
data/reproduced_data/20261024/                 rerun on amd133 (c6525-25g)
plots/paper_figure.pdf                         the figure in the paper
plots/reproduced_figure_20261024.pdf
```

The load and sweep scripts here are shared: Figures 2a and 9 call them too.

## Rerun the experiment (CloudLab node)

```bash
bash run_fig19a.sh    # -> data/reproduced_data/<date>/, plots/reproduced_figure_<date>.pdf
```

Node setup: `bench/scripts/setup_node.sh` (OzoneDB, ZFS `/tank`) and
`bench/scripts/local/build_baselines.sh` (RocksDB 9.6.1, SQLite variants, YCSB-cpp
commit `8401bde`). About 1.5 hours. The scripts work in
`$OZONEDB_HOME/bench/results/local/fig1-single-writer-local` (the tmpfs load has
that path hardcoded), moving an older one aside first, and copy the results to
the output folder at the end.

## Redraw a plot

```bash
cd scripts
python3 plot_single_writer.py --mode fig1csv \
  --results-dir ../data/paper_data/fig1-single-writer-local --output <file>   # = paper_figure.pdf
```

Needs matplotlib. `fig1csv` is the mode that matches the paper; the other modes
expect `.result` files this folder doesn't have.
