# Figure 12: cost model (OzoneKV vs Cassandra)

Paper: `images/experiment/fig_cost_col.pdf` (§5).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper (identical to `bench/fig_cost_col.pdf`) |
| `bench/results-cost2-20260828*.tsv` | measured cost coefficients (main TSV plus zipf / prefix / controls / projection variants) |
| `bench/scripts/plot/paper_cost_figure.py` | paper figure script (must sit next to `plot_cost_model.py`) |
| `bench/scripts/plot/plot_cost_model.py` | the cost model, plus a full diagnostic plot |
| `bench/scripts/plot/prices.json`, `space.json` | AWS list prices (as of 2026-08-26) and measured space factors |
| `bench/scripts/extract_cost_coefficients.py`, `server_sampler.sh` | turn raw runs into the coefficient TSV |
| `bench/scripts/campaign-cost2/` | the measurement campaign (`README.md`, `chain_*.sh`, `summarize.py`) |
| `bench/PLAN-cost-2.md`, `bench/RESULTS-cost.md` | plan and results write-up |

## Reproduce the plot (verified)

From this folder:

```bash
python3 bench/scripts/plot/paper_cost_figure.py \
    bench/results-cost2-20260828.tsv bench/scripts/plot/prices.json \
    --space bench/scripts/plot/space.json --tier-variant ch64k-adm \
    --cassandra-mode serial --read-fraction 0.10 --out bench/fig_cost
# writes bench/fig_cost.{pdf,png} (two panels) and bench/fig_cost_col.{pdf,png} (the paper figure)
```

Needs matplotlib (or use `uv run`). The output matches the paper figure; the PDF
bytes differ only when your matplotlib version differs from the one used for
the paper (3.11.1).

## Regenerate the data

Run the cost2 campaign (`bench/scripts/campaign-cost2/README.md`), then
`extract_cost_coefficients.py` to rebuild the TSV.
