#!/usr/bin/env bash
# Reproduce Figure 19a (local single writer) on a node prepared by
#   $OZONEDB_HOME/bench/scripts/setup_node.sh              (OzoneDB, ZFS /tank)
#   $OZONEDB_HOME/bench/scripts/local/build_baselines.sh   (RocksDB, SQLite variants, YCSB-cpp)
#
#   bash run_fig19a.sh [out_dir]       # default: data/reproduced_data/<date>
#
# Same settings as the paper: load 1M x 1 KB records for the 5 systems (on /tank
# and on tmpfs), then YCSB A, B, C, D, F with 1 writer, 120 s, 1 repeat.
# About 1.5 hours. Run it inside tmux.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
export OZONEDB_HOME="${OZONEDB_HOME:-$HOME/ozonedb}"
DATE="$(date +%Y%m%d)"
OUT="${1:-$HERE/data/reproduced_data/$DATE}"
[ -z "$(ls -A "$OUT" 2>/dev/null)" ] || { echo "$OUT already has results; pass another out_dir" >&2; exit 1; }

# The load and sweep scripts write to this fixed folder (the tmpfs load has it
# hardcoded); move an older one aside, run there, then copy to $OUT.
WORK="$OZONEDB_HOME/bench/results/local/fig1-single-writer-local"
if [ -d "$WORK" ] && [ -n "$(ls -A "$WORK")" ]; then
  mv "$WORK" "$WORK.old_$(date +%Y%m%d_%H%M%S)"
  echo "moved the existing $WORK aside"
fi
mkdir -p "$WORK"

echo "=== [1/4] load on /tank  ($(date))"
python3 "$HERE/scripts/run_load.py" --results-dir "$WORK" 2>&1 | tee "$WORK/load.log"

echo "=== [2/4] load on tmpfs  ($(date))"
python3 "$HERE/scripts/run_load_tmpfs.py" 2>&1 | tee "$WORK/load_tmpfs.log"

echo "=== [3/4] run phase, 1 writer  ($(date))"
python3 "$HERE/scripts/run_sweep.py" \
  --systems ozonedb,trunkcpp,bcw2,hctree,rocksdb \
  --workloads a,b,c,d,f --threads 1 --repeats 1 --duration 120 \
  --results-dir "$WORK" 2>&1 | tee "$WORK/sweep_t1.log"

echo "=== [4/4] plot"
mkdir -p "$(dirname "$OUT")" && cp -r "$WORK" "$OUT"
python3 "$HERE/scripts/plot_single_writer.py" --mode fig1csv --results-dir "$OUT" --output "$HERE/plots/reproduced_figure_$DATE.pdf"

echo
echo "done ($(date)). Results: $OUT   Plot: plots/reproduced_figure_$DATE.pdf"
