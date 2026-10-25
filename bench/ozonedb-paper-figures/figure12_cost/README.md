# Figure 12: cost model (OzoneKV vs Cassandra)

Paper: `images/experiment/fig_cost_col.pdf` (§5). The figure shows the projected cloud cost
against the dataset size. It compares OzoneKV with Cassandra (SERIAL) on EBS gp3 and on local
NVMe. The load is 10,000 ops/s with 10 % reads and 90 % blind writes, RF=3.

| File | What it is |
| --- | --- |
| `paper_figure.pdf` | the figure as it appears in the paper |
| `data/results-cost-20260828.tsv` | the per-cell cost coefficients from experiment cost-20260828: OzoneKV and Cassandra at 10 GB and 100 GB. This file is the input of the plot |
| `data/results-cost-20260828-{zipf,prefix,controls,projection,zipf-projection}.tsv` | the other tables of the experiment: key-space zipfian twins, pre-fix cells, controls and projections. Also under `bench/` |
| `data/prices.json`, `data/space.json` | the AWS list prices (as of 2026-08-26) and the projection parameters. The space measurements come from the 100 GB loads. The `_comment` key gives the provenance |
| `scripts/paper_cost_figure.py` | the paper figure script. It imports `plot_cost_model.py` from its own directory |
| `scripts/plot_cost_model.py` | the cost model, plus a full diagnostic plot |
| `scripts/extract_cost_coefficients.py`, `scripts/ozonekv/server_sampler.sh` | the scripts that turn raw runs into the coefficient TSV |
| `scripts/experiment-cost/` | `extract_all.sh`, `summarize.py` and `zipf_check.py`. `extract_all.sh` rebuilds every TSV from the raw result dirs |
| `scripts/experiment-cost-sweeps/` | the sweep scripts that run the cells of the experiment, and a `README.md` |
| `scripts/ozonekv/`, `scripts/cassandra/` | the load and run scripts that the sweep scripts call |

## Reproduce the plot

```bash
cd scripts
python3 paper_cost_figure.py ../data/results-cost-20260828.tsv ../data/prices.json \
    --space ../data/space.json --tier-variant ch64k-adm --cassandra-mode serial \
    --read-fraction 0.10 --out ../fig_cost
# writes ../fig_cost.{pdf,png} (two panels) and ../fig_cost_col.{pdf,png} (the paper figure)
```

## Regenerate the data (cluster)

Set `OZONEDB_HOME`, `${NODE_A}`, `${NODE_B}`, `${NODE_C}` and `${NODE_*_HOST}`, then run the
sweeps. See `scripts/experiment-cost-sweeps/README.md`. The sweeps take days of
wall-clock time. Then run `scripts/experiment-cost/extract_all.sh`
to rebuild the TSVs. It writes them as `bench/results-cost-*.tsv`. Move them into `data/`.
