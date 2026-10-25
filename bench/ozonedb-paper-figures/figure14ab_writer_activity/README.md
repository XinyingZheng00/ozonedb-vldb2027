# Figure 14a/14b: writer activity during compaction (8 and 16 writers)

Paper: `images/experiment/writer_activity_varyW_n8_r0pct.pdf` (`\label{fig:8writer}`) and
`writer_activity_varyW_n16_r0pct.pdf` (`\label{fig:16writer}`) (§5).

| File | What it is |
|---|---|
| `paper_figure_14a.pdf`, `paper_figure_14b.pdf` | the two panels as they appear in the paper. They are identical to the PDFs in `data/.../plots/`. |
| `data/v4_20260430T213314/per_trial/varyW_n8_r0pct/`, `varyW_n16_r0pct/` | the per-writer logs and `task_log_timeline.json` for the two trials |
| `scripts/plot_writer_activity.py` | the plot script |
| `scripts/churn_runner.py` and the rest | the same experiment runner as figure 13 |

This figure uses the same run as figure 13 (`v4_20260430T213314`). The plots
show only these two trials.

## Reproduce the plot

```bash
python3 scripts/plot_writer_activity.py data/v4_20260430T213314/per_trial/varyW_n8_r0pct    # 14a
python3 scripts/plot_writer_activity.py data/v4_20260430T213314/per_trial/varyW_n16_r0pct   # 14b
# This writes data/v4_20260430T213314/plots/writer_activity_<trial>.pdf.
```

The `--ymax <seconds>` option gives both panels the same y scale. The
`--no-ylabel` option removes the y label for a right-hand panel. The script
overwrites the PDFs in `data/.../plots/`.

## Regenerate the data

See `figure13_dist_compaction/README.md`. It is the same run.
