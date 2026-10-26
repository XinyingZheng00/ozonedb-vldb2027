# Figures 13 and 14a/14b: distributed compaction

- Figure 13: `images/experiment/completion_time_vs_writers.pdf`, `\label{fig:dist_compaction}`
- Figure 14a: `writer_activity_varyW_n8_r0pct.pdf` (`\label{fig:8writer}`), 8 writers
- Figure 14b: `writer_activity_varyW_n16_r0pct.pdf` (`\label{fig:16writer}`), 16 writers


## Rerun the experiment (CloudLab node)

```bash
bash run_fig13_14ab.sh    # -> data/reproduced_data/<date>/, plots/reproduced_figure_{13,14a,14b}_<date>.pdf
```

Paper settings (`run_config.json`): load 1M x 1 KB records with compaction off,
then N = 1, 2, 4, 8, 16 writers compact that backlog (abort rate 0, 1 repeat,
600 s cap); about 30 minutes. Needs OzoneDB built (`bench/scripts/setup_node.sh`)
and `/tank`. The six churn source patches are already in the committed OzoneDB code. Compaction settings are in `scripts/churn_config.json`.

## Redraw a plot

```bash
R=data/paper_data/v4_20260430T213314
python3 scripts/make_plots.py $R/trials.csv --plot completion_time_vs_writers --out <file>         # 13
python3 scripts/plot_writer_activity.py $R/per_trial/varyW_n8_r0pct --out <file>                   # 14a
python3 scripts/plot_writer_activity.py $R/per_trial/varyW_n16_r0pct --no-ylabel --out <file>      # 14b
```

Without `--out` (and `--plot`), both scripts write into the run folder's `plots/` instead and overwrite what's there.
