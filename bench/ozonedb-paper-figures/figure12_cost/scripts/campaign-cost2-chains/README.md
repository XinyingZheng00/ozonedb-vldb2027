# Campaign cost2-20260828 drivers (bench/PLAN-cost-2.md)

These are the chains that ran the campaign, as run. Each chain reads OZONEDB_HOME from
the environment and logs to bench/results/local/cost2-chains/. Export OZONEDB_HOME before
a rerun. Launch a chain from the laptop with
`nohup bash chain_X.sh > log 2>&1 < /dev/null & disown`. macOS has no setsid.

chain_oz100.sh takes the load writer count (16). On a new experiment, resume this chain
from its RAM-cells step. summarize.py prints one line per cell from the extractor.
extract_all.sh is Task 8.
