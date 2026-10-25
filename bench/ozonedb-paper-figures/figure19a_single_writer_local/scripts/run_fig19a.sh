#!/usr/bin/env bash
# Run the Figure 19a experiment on a node that these two scripts prepared:
#   $OZONEDB_HOME/bench/scripts/setup_node.sh              (OzoneDB, /tank)
#   $OZONEDB_HOME/bench/scripts/local/build_baselines.sh   (RocksDB, SQLite variants, YCSB-cpp)
#
#   bash run_fig19a.sh
#
# The steps and settings are the same as in the paper's run (2026-04-28):
#   1. Load 1M x 1 KB records into each system on /tank. Writes load_results.csv.
#      The loaded DBs become the cached_* copies that the run phase restores.
#   2. Do the same load on tmpfs (/dev/shm). Writes load_tmpfs_results.csv.
#   3. Run YCSB A, B, C, D, F with 1 writer, 120 s, 1 repeat. Writes results.csv and per-run logs.
# Systems: ozonedb, rocksdb, trunkcpp (SQLite WAL), bcw2 (BEGIN CONCURRENT + WAL2), hctree.
# The run takes about 1.5 hours. Run it inside tmux, so that a dropped SSH session does not stop it.
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
