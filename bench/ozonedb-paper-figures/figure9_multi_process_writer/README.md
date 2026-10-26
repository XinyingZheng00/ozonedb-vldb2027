# Figure 9: local multi-process writers

Paper: `images/experiment/fig2_multi_process_bars.pdf`, `\label{fig:local_multi_process_writer}` (§5).

```
run_fig9.sh                                    run the experiment and draw the plot
scripts/run_phase1_shared_db_sweep.py          N processes on one shared DB (ozonedb, trunkcpp, bcw2; N = 2, 4, 8, 16)
scripts/run_phase1_multiproc_sweep.py          independent-DB variant; holds helpers the shared-DB sweep imports
scripts/plot_phase1_paper.py                   plot script
(load scripts)                                 ../figure19a_single_writer_local/scripts/ (shared local YCSB scripts)
data/paper_data/phase1-multi-process/          the paper's data: results.csv, per-run logs/, sweep.log
data/reproduced_data/20261024/                 rerun on amd133 (c6525-25g): results.csv, logs/
plots/paper_figure.pdf                         the figure in the paper
plots/reproduced_figure_20261024.pdf
```

## Rerun the experiment (CloudLab node)

```bash
bash run_fig9.sh      # -> data/reproduced_data/<date>/, plots/reproduced_figure_<date>.pdf
```

Paper settings: workloads A, B, C, D, F; 90 s; 1 repeat;
1M x 1 KB records; about 2 hours. Node setup: `bench/scripts/setup_node.sh` and
`bench/scripts/local/build_baselines.sh`. The databases are loaded first if
`/tank/ycsb_data` doesn't have them.

## Redraw a plot

```bash
cd scripts
python3 plot_phase1_paper.py --proc-csv ../data/paper_data/phase1-multi-process/results.csv --out <file>   # = paper_figure.pdf
# without --out: --out-dir <dir> also draws multi_process_lines.pdf (not in the paper)
```

Needs matplotlib and numpy.
