#!/bin/bash
# Edited for the artifact: ${NODE_A} ${NODE_B} ${NODE_C} (user@host) and
# ${NODE_A_HOST} ${NODE_B_HOST} ${NODE_C_HOST} replace the CloudLab login
# and the three node hostnames. OZONEDB_HOME comes from the
# environment. Everything else is as run.
# One Cassandra cell: workload ai (50 % read, 50 % blind insert), quorum, 10 GB, 120 s.
# Twin of the quorum workload-a cell of tag cost2-20260828-cass-10g-rerun on the same box.
# Cassandra's YCSB update is already a blind CQL UPDATE. The insert/update difference
# costs OzoneDB half its GETs. This pair shows whether that difference exists for
# Cassandra at all. Corfu must be down. Never run both at once. Runs detached on the laptop.
set -euo pipefail
trap 'echo "[chain $(date "+%F %T")] CHAIN-FAILED rc=$? line $LINENO"' ERR
export OZONEDB_HOME=${OZONEDB_HOME:?set OZONEDB_HOME to the ozonedb checkout}
cd "$OZONEDB_HOME"
TAG=cost2-20260828
SRV=${NODE_A}
HOSTS=$(python3 bench/scripts/ycsb_config.py --list clients --field ssh | paste -s -d, -)
step() { echo "[chain $(date '+%F %T')] $*"; }

step "guard: corfu down, the 10 GB load snapshot present"
ssh -o BatchMode=yes "$SRV" 'pkill -KILL -f "org.corfudb.infrastructure.[C]orfuServer" || true; sleep 1; if pgrep -af "[C]orfuServer"; then echo "corfu still up"; exit 1; fi; echo "server clear"; du -sh /tank/cassandra/data.load /tank/cassandra/data.load-10g; df -h /tank/ssd | tail -1'

step "cell: cassandra quorum, workload ai, 10 GB, 120 s (tag $TAG-cass-ins), restored from data.load"
bash bench/scripts/local/run_multinode_ycsb_with_cassandra.sh --consistency quorum --record-cnt 10000000 \
  --workloads ai --writers-list 1 --client-hosts "$HOSTS" --trial 1 --duration 120 --run-tag $TAG-cass-ins

step "stop cassandra"
ssh -o BatchMode=yes "$SRV" '/tank/cassandra/cassandra_ctl.sh stop || true; df -h /tank/ssd | tail -1'
step "CHAIN-DONE"
