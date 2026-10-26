#!/usr/bin/env bash
# Run the Figure 17 (Serval Mesh / Rhizome) experiment on a node where
# OzoneDB is built ($OZONEDB_HOME/build/libOzoneDB.so must exist).
#
#   bash run_fig17.sh [out_dir]          # default: data/reproduced_data/<date>
#
# 1. builds servald from the $OZONEDB_HOME/bench/usecases/serval-dna submodule
#    (our fork, branch ozonedb-backend), with the OzoneDB Rhizome backend linked in
# 2. runs the paper's sweep (data/paper_data/analysis.md): N = 1..32 CLI workers
#    racing one servald daemon, 30 s per trial, persistent adder every 100 ms,
#    backends sqlite, ozonedb, sqlite_rest, 3 repeats
# Results go to a new folder; the paper's data/paper_data/persistent_adder_results
# is left untouched. Takes about an hour. Run it inside tmux.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
export OZONEDB_HOME="${OZONEDB_HOME:-$HOME/ozonedb}"
SERVAL="$OZONEDB_HOME/bench/usecases/serval-dna"
DATE="$(date +%Y%m%d)"
OUT="${1:-$HERE/data/reproduced_data/$DATE}"
[ -z "$(ls -A "$OUT" 2>/dev/null)" ] || { echo "$OUT already has results; pass another out_dir" >&2; exit 1; }
[ -f "$OZONEDB_HOME/build/libOzoneDB.so" ] || { echo "build OzoneDB first" >&2; exit 1; }
[ -f "$SERVAL/Makefile.in" ] || git -C "$OZONEDB_HOME" submodule update --init bench/usecases/serval-dna
[ -f "$SERVAL/ozonedb_shim.cpp" ] || { echo "$SERVAL isn't the ozonedb-backend fork" >&2; exit 1; }

echo "=== [1/4] build dependencies"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  autoconf automake libtool build-essential python3-requests

echo "=== [2/4] servald with the OzoneDB Rhizome backend"
cd "$SERVAL"
# Rebuild unless servald already links this node's OzoneDB. Stale outputs from
# another machine (servald, ozonedb_shim.o, a Makefile pointing at another
# OzoneDB build) are removed so configure regenerates the Makefile from Makefile.in.
if ! ldd ./servald 2>/dev/null | grep -q "$OZONEDB_HOME/build/libOzoneDB.so"; then
  rm -f servald ozonedb_shim.o Makefile
  autoreconf -f -i -I m4
  ./configure
  make -j"$(nproc)" servald
fi
ldd ./servald | grep -q "$OZONEDB_HOME/build/libOzoneDB.so" \
  || { echo "servald isn't linked against $OZONEDB_HOME/build/libOzoneDB.so" >&2; exit 1; }

echo "=== [3/4] sweep -> $OUT  ($(date))"
mkdir -p "$(dirname "$OUT")"
cd "$HERE/scripts"
python3 run_persistent_adder_benchmark.py \
  --workers 1,2,4,8,16,20,24,28,32 --duration-s 30 \
  --adder-interval-ms 100 --backends sqlite,ozonedb,sqlite_rest \
  --repeats 3 --out-dir "$OUT" 2>&1 | tee "$OUT.log"
mv "$OUT.log" "$OUT/run.log"

echo "=== [4/4] plot"
python3 -m pip install -q --user matplotlib
python3 plot_serval_combined.py --csv "$OUT/trials.csv" --out "$HERE/plots/reproduced_figure_$DATE.pdf"

echo
echo "done ($(date)). Results: $OUT   Plot: plots/reproduced_figure_$DATE.pdf"
