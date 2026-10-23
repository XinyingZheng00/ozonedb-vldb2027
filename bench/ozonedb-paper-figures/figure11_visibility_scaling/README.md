# Figure 11: visibility scaling

Paper: `images/experiment/visibility_scaling.pdf`, `\label{fig:visibility_scaling}` (§5.3).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper |
| `bench/PLAN-visibility.md` | experiment plan |
| `bench/scripts/local/run_visibility_cross_node.sh` | cross-node visibility run |
| `bench/scripts/local/run_consistency_with_corfu.sh` | consistency run against Corfu-backed OzoneDB |
| `bench/scripts/consistency.py`, `visibility_analysis.py` | run driver and analysis |
| `bench/scripts/plot/plot_consistency.py` | plot script (`staleness`, `lost-updates`, `visibility`, `table` modes) |

## Reproduce the plot

```bash
python3 bench/scripts/plot/plot_consistency.py visibility <visibility-result-dirs...> --out paper_figure.pdf
```

Each result folder must contain the `visibility.csv` written by the visibility run.

## Status: data missing

No visibility results are in this folder or anywhere under `revision/`. Rerun
following `bench/PLAN-visibility.md`. The repo's GitHub branch
`origin/visibility` has the related plans.
