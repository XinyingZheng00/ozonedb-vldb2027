# Campaign strict100-20260830 (bench/PLAN-strict-100g.md)

The script runs one trial per invocation, three trials in all. Cassandra runs
first, then OzoneDB, because they share the server box. Before a rerun, edit
OZONEDB_HOME at the top.

Every cell is 300 s and starts with both OzoneDB caches empty. By the end of a
cell, the 16 GiB LRU reaches about 3 % and the 50 GiB tier about 15 %. That is
deliberate. Before you read the numbers, read the "Read this first" section of
the plan.

The smoke cell of 2026-08-30 measured the tier fill directly: 2.44 GB in 120 s.
The first 38 s of that were tailer catch-up with no reads. That is 29.8 MB/s of
read time. It matches the 30 MB/s that the plan predicted.

Launch the script detached from the laptop. macOS has no setsid:

    nohup bash bench/scripts/campaign-strict100/chain_trial.sh 1 \
      > bench/results/local/strict100-chains/trial1.log 2>&1 < /dev/null & disown

To watch it, run: tail -f bench/results/local/strict100-chains/trial1.log
The trial is done when the log holds CHAIN-DONE. The trial failed when the log
holds CHAIN-FAILED.
