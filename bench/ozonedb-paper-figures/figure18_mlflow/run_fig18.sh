#!/usr/bin/env bash
# Run the Figure 18 (MLflow) experiment on a node where OzoneDB is built
# ($OZONEDB_HOME/build/libozonedb_pyshim.so must exist).
#
#   bash run_fig18.sh [out_dir]
#
# Same settings as the paper's run:
#   P = 1..48 Ray Tune trial processes, 3 threads each (training, cpu/mem
#   monitor, disk monitor), 100 training steps on 2,000 samples, 1 repeat,
#   backends sqlite, ozonedb, postgres; MLflow's SQLite busy_timeout patched
#   to 5000 ms. One run records all three panels' metrics in trials.csv
#   (error_rate, postgres_peak_rss_mb, mean_compute_latency_ms).
# Takes roughly an hour. Run it inside tmux.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
export OZONEDB_HOME="${OZONEDB_HOME:-$HOME/ozonedb}"
DATE="$(date +%Y%m%d)"
OUT="${1:-$HERE/data/reproduced_data/$DATE}"
[ -z "$(ls -A "$OUT" 2>/dev/null)" ] || { echo "$OUT already has results; pass another out_dir" >&2; exit 1; }
[ -f "$OZONEDB_HOME/build/libozonedb_pyshim.so" ] \
  || { echo "build OzoneDB first: libozonedb_pyshim.so not found" >&2; exit 1; }

echo "=== [1/4] PostgreSQL, Python packages (versions used by the paper)"
if ! command -v psql >/dev/null; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql postgresql-contrib
fi
sudo systemctl start postgresql
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='ozonedb_bench'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE ROLE ozonedb_bench WITH LOGIN PASSWORD 'bench' SUPERUSER;"
python3 -m pip install -q --user "mlflow==3.15.1" "ray[tune]==2.57.0" psycopg2-binary psutil scikit-learn
export PATH="$HOME/.local/bin:$PATH"

echo "=== [2/4] MLflow SQLite busy_timeout 20000 -> 5000 ms (as in the paper's runs)"
UTILS="$(python3 -c 'import mlflow.store.db.utils as m; print(m.__file__)')"
sed -i 's/PRAGMA busy_timeout = 20000;/PRAGMA busy_timeout = 5000;/' "$UTILS"
grep -q 'PRAGMA busy_timeout = 5000;' "$UTILS" \
  || { echo "busy_timeout patch didn't apply to $UTILS" >&2; exit 1; }

echo "=== [3/4] P-sweep -> $OUT  ($(date))"
mkdir -p "$OUT"
cd "$HERE/scripts"
python3 run_ray_swapin.py --processes 1,2,4,8,16,20,24,28,32,48 \
  --k-steps 100 --n-samples 2000 --cpus-per-trial 0.3 --repeats 1 \
  --backends sqlite,ozonedb,postgres \
  --with-monitor --monitor-interval-s 0.5 --with-disk-monitor --disk-monitor-interval-s 0.5 \
  --out-dir "$OUT" 2>&1 | tee "$OUT/run.log"

echo "=== [4/4] plot"
python3 plot_combined.py --csv "$OUT/trials.csv" --out "$HERE/plots/reproduced_figure_$DATE.pdf"

echo
echo "done ($(date)). Results: $OUT   Plot: plots/reproduced_figure_$DATE.pdf"
