#!/usr/bin/env bash
# Reproduce Figure 15 (generation distribution under churn) and Table 4
# (average reassignments per abort ratio) on a node where OzoneDB is built and
# /tank exists. They come from one experiment with one configuration.
#
#   bash run_fig15_table4.sh [out_dir]       # default: data/reproduced_data/<date>
#
# Same settings as the paper's run (data/paper_data/v4_20260501T003206/run_config.json):
# N = 4 writers, abort ratio 1, 5, 10, 25, 50 %, 5 trials each, 20 s quiescence
# grace, 1M x 1 KB records. Uses the churn scripts in ../figure13_14ab_dist_compaction/scripts
# (the same runner as Figures 13/14, varying the abort rate instead of the writers).
#   Figure 15 = generation distribution                  -> plots/reproduced_figure_15_<date>.pdf
#   Table 4   = mean n_tasks_with_reassignment per ratio -> plots/reproduced_table_4_<date>.txt
# About 1.5 hours. Run it inside tmux.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CHURN="$HERE/../figure13_14ab_dist_compaction/scripts"
export OZONEDB_HOME="${OZONEDB_HOME:-$HOME/ozonedb}"
DATE="$(date +%Y%m%d)"
OUT="${1:-$HERE/data/reproduced_data/$DATE}"
[ -z "$(ls -A "$OUT" 2>/dev/null)" ] || { echo "$OUT already has results; pass another out_dir" >&2; exit 1; }
[ -f /usr/lib/libozonedb.so ] || { echo "build OzoneDB first (bench/scripts/update_jni.sh)" >&2; exit 1; }
[ -d /tank ] || { echo "/tank is missing" >&2; exit 1; }
mkdir -p "$OUT"

cd "$CHURN"
python3 churn_runner.py --vary abort --abort-rates 0.01,0.05,0.1,0.25,0.5 \
  --fixed-n 4 --repeats 5 --quiescence-grace 20 --record-count 1000000 --key-size 1KB \
  --completion-timeout 600 --results-dir "$OUT" 2>&1 | tee "$OUT/run.log"

python3 "$CHURN/make_plots.py" "$OUT/trials.csv" --plot generation_dist \
  --out "$HERE/plots/reproduced_figure_15_$DATE.pdf"                                  # Figure 15
python3 "$HERE/scripts/table4.py" "$OUT/trials.csv" \
  --out "$HERE/plots/reproduced_table_4_$DATE.txt"                                    # Table 4

echo
echo "done ($(date)). Results: $OUT"
echo "Outputs: plots/reproduced_figure_15_$DATE.pdf, plots/reproduced_table_4_$DATE.txt"
