#!/bin/bash
# Measure the OzoneKV cells at 100 GB:
#   - the 100 GB load with 16 writers
#   - the RAM cells
#   - the tier cells
#   - the key-space zipfian cells
# Then the script runs the Cassandra cz and az cells at 10 GB from data.load-10g.
# The script expects the data.load-10g snapshot, which sweep_verify.sh makes.
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
C0=${NODE_C}
HOSTS=$(python3 bench/scripts/ycsb_config.py --list clients --field ssh | paste -s -d, -)
step() { echo "[sweep $(date '+%F %T')] $*"; }
RUN=bench/scripts/local/run_multinode_ycsb_with_corfu.sh
RC=100000000
R=bench/results/local

step "guard: nothing else on the server"
ssh -o BatchMode=yes "$SRV" '/tank/cassandra/cassandra_ctl.sh stop >/dev/null 2>&1 || true; pkill -KILL -f "org.corfudb.infrastructure.[C]orfuServer" || true; sleep 1; if pgrep -af "[C]assandraDaemon|[C]orfuServer"; then echo "server busy"; exit 1; fi; echo "server clear"; df -h /tank/ssd | tail -1; ls -d /tank/cassandra/data.load-10g'

step "archive the previous 100 GB load sample (space.json source) before the new load overwrites it"
mkdir -p $R/archive-old-cluster/_server
for f in $R/_server/*/ozonedb-corfu-native_*rc100000000*; do [[ -e "$f" ]] && mv "$f" $R/archive-old-cluster/_server/; done
ls $R/archive-old-cluster/_server/ || true

step "100 GB trimmed load, 16 writers"
bash bench/scripts/local/load_corfu_dataset.sh --writers 16 --record-cnt $RC --log-trim
ssh -o BatchMode=yes "$SRV" 'du -sh /mnt/corfu/*; df -h /tank/ssd | tail -1'
step "pull the 16 writer files from the load host (the orchestrator does not)"
scp -q -o BatchMode=yes "$C0:~/ozonedb/bench/results/local/1KB-${RC}-insert-*" $R/ && ls $R/ | grep -c "1KB-${RC}-insert"

step "RAM cells, workload c at 800 MiB (900 s), 100 MiB / 12.5 MiB (600 s)"
bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes 838860800 --workloads c --writers-list 1 \
  --client-hosts "$HOSTS" --trial 1 --duration 900 --run-tag $TAG-100g
for c in 104857600 13107200; do
  bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes $c --workloads c --writers-list 1 \
    --client-hosts "$HOSTS" --trial 1 --duration 600 --run-tag $TAG-100g
done
step "workload a at 100 MiB, 600 s"
bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes 104857600 --workloads a --writers-list 1 \
  --client-hosts "$HOSTS" --trial 1 --duration 600 --run-tag $TAG-100g

step "tier cells behind 800 MiB RAM: 2.5 GiB c 300 s, 25 GiB c 1800 s, 50 GiB a c 2700 s"
run_tier100() {
  bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes 838860800 --disk-cache-bytes $1 --workloads "$2" \
    --writers-list 1 --client-hosts "$HOSTS" --trial 1 --duration $3 --run-tag $TAG-100g-tier
}
run_tier100 2684354560  c     300
run_tier100 26843545600 c     1800
run_tier100 53687091200 "a c" 2700

step "100 GB key-space zipfian: cz at 800 MiB / 100 MiB / 12.5 MiB 300 s, az at 100 MiB 600 s, 25 GiB tier cz 1800 s"
for c in 838860800 104857600 13107200; do
  bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes $c --workloads cz --writers-list 1 \
    --client-hosts "$HOSTS" --trial 1 --duration 300 --run-tag $TAG-100g-zipf
done
bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes 104857600 --workloads az --writers-list 1 \
  --client-hosts "$HOSTS" --trial 1 --duration 600 --run-tag $TAG-100g-zipf
bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes 838860800 --disk-cache-bytes 26843545600 --workloads cz \
  --writers-list 1 --client-hosts "$HOSTS" --trial 1 --duration 1800 --run-tag $TAG-100g-zipf

step "Cassandra cz / az at 10 GB, quorum, from data.load-10g"
ssh -o BatchMode=yes "$SRV" "pkill -KILL -f 'org.corfudb.infrastructure.[C]orfuServer' || true"
ssh -o BatchMode=yes "$SRV" '/tank/cassandra/cassandra_ctl.sh stop >/dev/null 2>&1 || true; rm -rf /tank/cassandra/data; cp -a /tank/cassandra/data.load-10g /tank/cassandra/data; du -sh /tank/cassandra/data'
bash bench/scripts/local/run_multinode_ycsb_with_cassandra.sh --consistency quorum --record-cnt 10000000 --no-restore \
  --workloads "cz az" --writers-list 1 --client-hosts "$HOSTS" --trial 1 --duration 120 --run-tag $TAG-cass-zipf
ssh -o BatchMode=yes "$SRV" '/tank/cassandra/cassandra_ctl.sh stop; df -h /tank/ssd | tail -1'

step "SWEEP-DONE"
