# Figure 10: multi-node writers

Paper: `images/experiment/multi_throughput.pdf`, `\label{fig:3_coordination}` (§5.3).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper |
| `bench/results-strict100-20260830.tsv` | summary of campaign strict100-20260830: steady ops/s per (system, workload, writers, trial) for `cassandra-serial` and `ozonedb-corfu-linearizable-native-lru16g-dc50g-ch64k-adm`; workloads a, b, c, d, f; 2-32 writers; 3 trials |
| `bench/scripts/campaign-strict100/` | how the campaign was run (`chain_trial.sh`, `README.md`) |
| `bench/scripts/local/run_multinode_ycsb*.{py,sh}` | multi-node YCSB launchers (Corfu-backed OzoneDB, Cassandra) |
| `bench/scripts/plot/plot_throughput.py` | multi-node plot script (faceoff / scaling modes) |
| `bench/scripts/plot/plot_ozonedb_vs_slatedb.py` | older OzoneDB-vs-SlateDB variant |

## Reproduce the plot

`plot_throughput.py` reads the raw per-writer `.result` folders, not the TSV:

```bash
python3 bench/scripts/plot/plot_throughput.py <result-dirs...> --mode scaling \
  --relabel <dir>=ozonedb-corfu-linearizable --out paper_figure.pdf
```

## Status: partial

- The raw `.result` folders are not here, only the summary TSV.
- The TSV has Cassandra and OzoneDB but not CR-SQLite, which the paper figure also shows.
- No script here draws the figure straight from the TSV.

To rerun the campaign, follow `bench/scripts/campaign-strict100/README.md`
(edit `OZONEDB_HOME` first). More plans and results for this figure are in the
repo's GitHub branches `origin/worktree-plan-strict-100g` and `origin/visibility`.
