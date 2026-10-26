#!/usr/bin/env bash
# Reproduce Figure 13 (distributed compaction) and Figures 14a/14b (writer
# activity at N=8 and N=16) on a node where OzoneDB is built and /tank exists.
# They come from one experiment with one configuration.
#
#   bash run_fig13_14ab.sh [out_dir]       # default: data/reproduced_data/<date>
#
# Same settings as the paper's run (data/paper_data/v4_20260430T213314/run_config.json):
# load 1M x 1 KB records with compaction disabled, snapshot that backlog, then
# for N = 1, 2, 4, 8, 16 writers restore the snapshot and time how long the N
# writers take to compact it (abort rate 0, 1 repeat, 600 s cap per trial).
#   Figure 13  = completion time vs writers          (trials.csv)
#   Figure 14a = writer activity at N=8, 14b = N=16  (per_trial/varyW_n8_r0pct, _n16_)
# The OzoneDB code needs the six churn patches; they're already committed.
# About 30 minutes. Run it inside tmux.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
export OZONEDB_HOME="${OZONEDB_HOME:-$HOME/ozonedb}"
DATE="$(date +%Y%m%d)"
OUT="${1:-$HERE/data/reproduced_data/$DATE}"
[ -z "$(ls -A "$OUT" 2>/dev/null)" ] || { echo "$OUT already has results; pass another out_dir" >&2; exit 1; }
[ -f /usr/lib/libozonedb.so ] || { echo "build OzoneDB first (bench/scripts/update_jni.sh)" >&2; exit 1; }
[ -d /tank ] || { echo "/tank is missing" >&2; exit 1; }
mkdir -p "$OUT"

cd "$HERE/scripts"
python3 churn_runner.py --vary writers --writers 1,2,4,8,16 \
  --record-count 1000000 --key-size 1KB --completion-timeout 600 \
  --fixed-r 0.0 --repeats 1 --results-dir "$OUT" 2>&1 | tee "$OUT/run.log"

python3 make_plots.py "$OUT/trials.csv" --plot completion_time_vs_writers \
  --out "$HERE/plots/reproduced_figure_13_$DATE.pdf"                                   # Figure 13
python3 plot_writer_activity.py "$OUT/per_trial/varyW_n8_r0pct" \
  --out "$HERE/plots/reproduced_figure_14a_$DATE.pdf"                                  # Figure 14a
python3 plot_writer_activity.py "$OUT/per_trial/varyW_n16_r0pct" --no-ylabel \
  --out "$HERE/plots/reproduced_figure_14b_$DATE.pdf"                                  # Figure 14b (right-hand panel)

echo
echo "done ($(date)). Results: $OUT"
echo "Plots: plots/reproduced_figure_{13,14a,14b}_$DATE.pdf"
