#!/usr/bin/env bash
# Run the Figure 19a experiment on a node prepared by
#   $OZONEDB_HOME/bench/scripts/setup_node.sh              (OzoneDB, /tank)
#   $OZONEDB_HOME/bench/scripts/local/build_baselines.sh   (RocksDB, SQLite variants, YCSB-cpp)
#
#   bash run_fig19a.sh
#
# Same steps and settings as the paper's run (2026-04-28):
#   1. load 1M x 1 KB records into each system on /tank  -> load_results.csv
#      (the loaded DBs become the cached_* copies the run phase restores)
#   2. the same load on tmpfs (/dev/shm)                  -> load_tmpfs_results.csv
#   3. YCSB A, B, C, D, F with 1 writer, 120 s, 1 repeat  -> results.csv + per-run logs
# Systems: ozonedb, rocksdb, trunkcpp (SQLite WAL), bcw2 (BEGIN CONCURRENT + WAL2), hctree.
# Takes about 1.5 hours. Run it inside tmux so a dropped SSH session doesn't stop it.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
export OZONEDB_HOME="$HOME/ozonedb"
OUT="$OZONEDB_HOME/bench/results/local/fig1-single-writer-local"
mkdir -p "$OUT"

echo "=== [1/3] load on /tank  ($(date))"
python3 "$HERE/run_fig2_load.py" --results-dir "$OUT" 2>&1 | tee "$OUT/load.log"

echo "=== [2/3] load on tmpfs  ($(date))"
python3 "$HERE/run_fig2_load_tmpfs.py" 2>&1 | tee "$OUT/load_tmpfs.log"

echo "=== [3/3] run phase, 1 writer  ($(date))"
python3 "$HERE/run_fig2_sweep.py" \
  --systems ozonedb,trunkcpp,bcw2,hctree,rocksdb \
  --workloads a,b,c,d,f --threads 1 --repeats 1 --duration 120 \
  --results-dir "$OUT" 2>&1 | tee "$OUT/sweep_t1.log"

echo
echo "done ($(date)). Results: $OUT"
ls "$OUT"
