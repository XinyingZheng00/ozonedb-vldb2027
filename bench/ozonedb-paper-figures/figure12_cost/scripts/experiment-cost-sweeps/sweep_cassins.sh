#!/bin/bash
# Measure one Cassandra cell: workload ai (50 % read, 50 % blind insert), quorum, 10 GB,
# 120 s. The cell is the pair of the quorum workload-a cell of tag
# cost-20260828-cass-10g-rerun.
# Cassandra's YCSB update is a blind CQL UPDATE. For OzoneKV, the insert/update
# difference removes half the GETs. This pair shows if the difference exists for
# Cassandra.
# WARNING: Corfu must be down. Never run Corfu and Cassandra at the same time.
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
SRV=${NODE_A}
HOSTS=$(python3 bench/scripts/ycsb_config.py --list clients --field ssh | paste -s -d, -)
step() { echo "[sweep $(date '+%F %T')] $*"; }

step "guard: corfu down, the 10 GB load snapshot present"
ssh -o BatchMode=yes "$SRV" 'pkill -KILL -f "org.corfudb.infrastructure.[C]orfuServer" || true; sleep 1; if pgrep -af "[C]orfuServer"; then echo "corfu still up"; exit 1; fi; echo "server clear"; du -sh /tank/cassandra/data.load /tank/cassandra/data.load-10g; df -h /tank/ssd | tail -1'

step "cell: cassandra quorum, workload ai, 10 GB, 120 s (tag $TAG-cass-ins), restored from data.load"
bash bench/scripts/local/run_multinode_ycsb_with_cassandra.sh --consistency quorum --record-cnt 10000000 \
  --workloads ai --writers-list 1 --client-hosts "$HOSTS" --trial 1 --duration 120 --run-tag $TAG-cass-ins

step "stop cassandra"
ssh -o BatchMode=yes "$SRV" '/tank/cassandra/cassandra_ctl.sh stop || true; df -h /tank/ssd | tail -1'
step "SWEEP-DONE"
