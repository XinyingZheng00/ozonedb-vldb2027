#!/usr/bin/env bash
# Reproduce Figure 9 (local multi-process writers) on a node prepared with
# bench/scripts/setup_node.sh and bench/scripts/local/build_baselines.sh.
#
#   bash run_fig9.sh [out_dir]          # default: data/reproduced_data/<date>
#
# Same settings as the paper's run (data/paper_data/WORKLOG.md): N = 2, 4, 8, 16 separate
# YCSB processes, each single-threaded, all on ONE shared database; systems
# ozonedb, trunkcpp, bcw2; workloads A, B, C, D, F; 90 s per run; 1 repeat;
# 1M x 1 KB records. About 2 hours. Run it inside tmux.
#
# The sweep restores the loaded databases from /tank/ycsb_data/cached_*. If they
# aren't there yet, this script loads them first with Figure 19a's load script.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
export OZONEDB_HOME="${OZONEDB_HOME:-$HOME/ozonedb}"
export YCSB_CPP_DIR="${YCSB_CPP_DIR:-$HOME/YCSB-cpp}"
DATE="$(date +%Y%m%d)"
OUT="${1:-$HERE/data/reproduced_data/$DATE}"
[ -z "$(ls -A "$OUT" 2>/dev/null)" ] || { echo "$OUT already has results; pass another out_dir" >&2; exit 1; }
D=/tank/ycsb_data
mkdir -p "$OUT"

if [ ! -d "$D/cached_data-ozonedb-1KB-1000000" ] || [ ! -f "$D/trunkcpp_ycsb/cached_trunkcpp_ycsb.db" ] \
   || [ ! -f "$D/bcw2_ycsb/cached_bcw_ycsb.db" ]; then
  echo "=== loading 1M records for ozonedb, trunkcpp, bcw2 ($(date))"
  python3 "$HERE/../figure19a_single_writer_local/scripts/run_load.py" \
    --systems ozonedb,trunkcpp,bcw2 --results-dir "$OUT/load"
fi

echo "=== multi-process sweep -> $OUT  ($(date))"
python3 "$HERE/scripts/run_phase1_shared_db_sweep.py" \
  --systems ozonedb,trunkcpp,bcw2 --workloads a,b,c,d,f \
  --processes 2,4,8,16 --repeats 1 --duration 90 \
  --results-dir "$OUT" 2>&1 | tee "$OUT/sweep.log"

python3 "$HERE/scripts/plot_phase1_paper.py" --proc-csv "$OUT/results.csv" --out "$HERE/plots/reproduced_figure_$DATE.pdf"

echo
echo "done ($(date)). Results: $OUT   Plot: plots/reproduced_figure_$DATE.pdf"
