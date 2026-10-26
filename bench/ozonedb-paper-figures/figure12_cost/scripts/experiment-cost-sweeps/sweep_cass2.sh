#!/bin/bash
# Measure Cassandra at 100 GB: the 100 GB load and its four cells.
# The script expects the 10 GB cells and the data.load-10g snapshot to exist.
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
RUN=bench/scripts/local/run_multinode_ycsb_with_cassandra.sh

step "guard: corfu down, cassandra down"
ssh -o BatchMode=yes "$SRV" "pkill -KILL -f 'org.corfudb.infrastructure.[C]orfuServer' || true; /tank/cassandra/cassandra_ctl.sh stop || true; ls -d /tank/cassandra/data.load-10g"

step "100 GB load (8 writers on the load host)"
bash bench/scripts/local/load_multinode_cassandra.sh --writers 8 --record-cnt 100000000
ssh -o BatchMode=yes "$SRV" '/tank/cassandra/cassandra_ctl.sh space; du -sk /tank/cassandra/data.load; df -h /tank/ssd | tail -1'

step "100 GB: workload c, quorum + serial, no restore"
for mode in quorum serial; do
  bash $RUN --consistency $mode --record-cnt 100000000 --no-restore --workloads c --writers-list 1 \
    --client-hosts "$HOSTS" --trial 1 --duration 120 --run-tag $TAG-cass-100g
done
step "100 GB: workload a, quorum + serial, restore before each"
for mode in quorum serial; do
  bash $RUN --consistency $mode --record-cnt 100000000 --workloads a --writers-list 1 \
    --client-hosts "$HOSTS" --trial 1 --duration 120 --run-tag $TAG-cass-100g
done

step "drop the 100 GB trees, put the 10 GB snapshot back as data.load"
ssh -o BatchMode=yes "$SRV" '/tank/cassandra/cassandra_ctl.sh stop || true; rm -rf /tank/cassandra/data /tank/cassandra/data.load && cp -a /tank/cassandra/data.load-10g /tank/cassandra/data.load && df -h /tank/ssd | tail -1'
step "SWEEP-DONE"
