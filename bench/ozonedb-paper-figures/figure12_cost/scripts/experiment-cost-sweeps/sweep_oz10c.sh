#!/bin/bash
# Measure the OzoneKV tier cells at 10 GB with a 1800 s duration.
# The 20 GiB tier at 10 GB needs about 11 min per writer to fill (miss rate x 64 KiB is
# about 30 MB/s). So the 600 s tier cells of sweep_oz10b.sh do not converge. This script
# runs workloads a and c again at 1800 s.
# Before you start the script, set these variables:
#   - OZONEDB_HOME: the root of this repository
#   - NODE_A, NODE_B, NODE_C: the SSH login (user@host) of each cluster node
#   - NODE_A_HOST, NODE_B_HOST, NODE_C_HOST: the hostname of each cluster node
# The script writes its log to bench/results/local/cost-sweeps/.
set -euo pipefail
trap 'echo "[sweep $(date "+%F %T")] SWEEP-FAILED rc=$? line $LINENO"' ERR
export OZONEDB_HOME=${OZONEDB_HOME:?set OZONEDB_HOME to the ozonedb checkout}
cd "$OZONEDB_HOME"
TAG=cost-20260828
SRV=${NODE_B}
HOSTS=$(python3 bench/scripts/ycsb_config.py --list clients --field ssh | paste -s -d, -)
step() { echo "[sweep $(date '+%F %T')] $*"; }
RUN=bench/scripts/local/run_multinode_ycsb_with_corfu.sh
RC=10000000

step "guard: nothing else on the server"
ssh -o BatchMode=yes "$SRV" '/tank/cassandra/cassandra_ctl.sh stop >/dev/null 2>&1 || true; pkill -KILL -f "org.corfudb.infrastructure.[C]orfuServer" || true; sleep 1; if pgrep -af "[C]assandraDaemon"; then echo "server busy"; exit 1; fi; echo "server clear"'

step "20 GiB tier behind 80 MiB RAM, a c, 1800 s each"
bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes 83886080 --disk-cache-bytes 21474836480 --workloads "a c" \
  --writers-list 1 --client-hosts "$HOSTS" --trial 1 --duration 1800 --run-tag $TAG-10g-tier
step "linearizable workload c at 5 GiB, 600 s (the 120 s cell closed 5 % full, its default twin 26 %)"
bash $RUN --log-trim --linearizable --record-cnt $RC --lru-cache-bytes 5368709120 --workloads c --writers-list 1 \
  --client-hosts "$HOSTS" --trial 1 --duration 600 --run-tag $TAG-10g
step "SWEEP-DONE"
