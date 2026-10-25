#!/bin/bash
# This script runs the extractions, the two model runs and the overlay.
#
# Corpus layout:
#   results-$TAG.tsv          the main corpus, one build: OzoneDB 10 GB (-10g-fix,
#                             -10g-fix-tier) and 100 GB (-100g, -100g-tier), Cassandra
#                             10 GB and 100 GB. The load rows come from the results root.
#   results-$TAG-controls.tsv scaling and JNI on the fixed build, and the Cassandra
#                             control cells on the new box (-cass-10g-rerun). Also the
#                             probe-counter diagnostic cells (-100g-diag: a, ai, c) and
#                             the insert pair on Cassandra (-cass-ins at 10 GB,
#                             -cass-100g-new at 100 GB). None of these feed the model.
#   results-$TAG-prefix.tsv   the cells on the build BEFORE the filter-copy fix
#                             (-10g, -10g-tier, -scale, -jni). This is the "before" control.
#   results-$TAG-zipf.tsv     key-space zipfian: -10g-zipf (old build, h is unaffected),
#                             -100g-zipf, -cass-zipf, plus -cass-10g and -10g-fix. The two
#                             extra tags let the model read cpuC and cpuO
#                             (--cpu-workload a) from the fixed build.
# Usage: extract_all.sh
set -euo pipefail
: "${OZONEDB_HOME:?OZONEDB_HOME must point at the repo root}"
cd "$OZONEDB_HOME"
TAG=cost2-20260828
R=bench/results/local
EX="python3 bench/scripts/extract_cost_coefficients.py"
PY="uv run --quiet --with matplotlib python3"
PRICES=bench/scripts/plot/prices.json
SPACE=bench/scripts/plot/space.json

have() { for d in "$@"; do [[ -d "$d" ]] && printf '%s ' "$d"; done; }
$EX $(have $R/$TAG-10g-fix $R/$TAG-10g-fix-tier $R/$TAG-100g $R/$TAG-100g-tier $R/$TAG-cass-10g $R/$TAG-cass-100g) $R \
  --window 60 --tsv bench/results-$TAG.tsv
$EX $(have $R/$TAG-10g-fix-scale $R/$TAG-10g-fix-jni $R/$TAG-cass-10g-rerun $R/$TAG-100g-diag \
  $R/$TAG-cass-ins $R/$TAG-cass-100g-new) --window 60 --tsv bench/results-$TAG-controls.tsv
$EX $(have $R/$TAG-10g $R/$TAG-10g-tier $R/$TAG-scale $R/$TAG-jni) --window 60 --tsv bench/results-$TAG-prefix.tsv
$EX $(have $R/$TAG-10g-zipf $R/$TAG-100g-zipf $R/$TAG-cass-zipf $R/$TAG-cass-10g $R/$TAG-10g-fix) $R --window 60 --tsv bench/results-$TAG-zipf.tsv

$PY bench/scripts/plot/plot_cost_model.py bench/results-$TAG.tsv $PRICES --space $SPACE \
  --tier-variant ch64k-adm --cassandra-mode serial \
  --out-dir bench/scripts/plot/out --table bench/results-$TAG-projection.tsv
$PY bench/scripts/plot/plot_cost_model.py bench/results-$TAG-zipf.tsv $PRICES --space $SPACE \
  --tier-variant ch64k-adm --h-workload cz --cpu-workload a --cassandra-mode serial \
  --out-dir bench/scripts/plot/out-zipf --table bench/results-$TAG-zipf-projection.tsv
$PY bench/scripts/plot/overlay_projections.py bench/results-$TAG-projection.tsv \
  bench/results-$TAG-zipf-projection.tsv --out bench/results-$TAG.png --no-other-tier
echo "EXTRACT-DONE"
