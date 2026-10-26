# Figure 15 and Table 4: churn recovery
- Figure 15: `images/experiment/generation_dist.pdf`, `\label{fig:generation_dist}`
- Table 4: `\label{tab:reassignment}`
## Rerun the experiment (CloudLab node)

```bash
bash run_fig15_table4.sh    # -> data/reproduced_data/<date>/, plots/reproduced_figure_15_<date>.pdf, plots/reproduced_table_4_<date>.txt
```

Paper settings (`run_config.json`): N = 4 writers, abort ratio 1, 5, 10, 25, 50 %,
5 trials each, 20 s quiescence grace, 1M x 1 KB records; about 1.5 hours. Needs
OzoneDB built (`bench/scripts/setup_node.sh`) and `/tank`. Abort injection uses
`OZONEDB_CHURN_ABORT_RATE` (one of the six committed churn patches). 

## Redraw the figure / recompute the table

```bash
R=data/paper_data/v4_20260501T003206
python3 ../figure13_14ab_dist_compaction/scripts/make_plots.py $R/trials.csv --plot generation_dist --out <file>   # Figure 15
python3 scripts/table4.py $R/trials.csv                                                                           # Table 4: 8.8, 44.4, 87.0, 211.8, 401.6
```

Use `data/reproduced_data/<date>/trials.csv` instead for a reproduced run.
`table4.py` prints the table (`--out <file>` also saves it). Each Table 4 value is
the mean of `n_tasks_with_reassignment` over the 5 trials at that abort ratio.
