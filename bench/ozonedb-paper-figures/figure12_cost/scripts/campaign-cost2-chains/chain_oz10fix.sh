#!/bin/bash
# Edited for the artifact: ${NODE_A} ${NODE_B} ${NODE_C} (user@host) and
# ${NODE_A_HOST} ${NODE_B_HOST} ${NODE_C_HOST} replace the CloudLab login
# and the three node hostnames. OZONEDB_HOME comes from the
# environment. Everything else is as run.
# New experiment 2026-08-29, after the filter-copy fix (3b28a2cd). This chain re-measures
# the 10 GB cells on the fixed build. The write-up quotes the throughput and CPU columns
# of these cells. The 10 GB snapshot is /mnt/corfu/load. chain_oz100c.sh reloaded it.
# Tags: -10g-fix (RAM, a, linearizable), -10g-fix-tier, -10g-fix-scale, -10g-fix-jni.
# The a@100m and c@80m cells are already in -10g-fix. Runs detached on the laptop after
# chain_oz100c.sh ends with CHAIN-DONE.
set -euo pipefail
trap 'echo "[chain $(date "+%F %T")] CHAIN-FAILED rc=$? line $LINENO"' ERR
export OZONEDB_HOME=${OZONEDB_HOME:?set OZONEDB_HOME to the ozonedb checkout}
cd "$OZONEDB_HOME"
TAG=cost2-20260828
SRV=${NODE_A}
HOSTS=$(python3 bench/scripts/ycsb_config.py --list clients --field ssh | paste -s -d, -)
hosts_n() { echo "$HOSTS" | tr ',' '\n' | head -n "$1" | paste -s -d, -; }
step() { echo "[chain $(date '+%F %T')] $*"; }
RUN=bench/scripts/local/run_multinode_ycsb_with_corfu.sh
RC=10000000

step "guard: cassandra down, nothing else on the server, 10 GB snapshot in /mnt/corfu/load"
ssh -o BatchMode=yes "$SRV" '/tank/cassandra/cassandra_ctl.sh stop >/dev/null 2>&1 || true; pkill -KILL -f "org.corfudb.infrastructure.[C]orfuServer" || true; sleep 1; if pgrep -af "[C]assandraDaemon|[C]orfuServer"; then echo "server busy"; exit 1; fi; echo "server clear"; du -sh /mnt/corfu/load /mnt/corfu/load-bucket; df -h /tank/ssd | tail -1'

step "RAM h cells, workload c: 640 MiB / 10 MiB / 1.25 MiB, 300 s"
for c in 671088640 10485760 1310720; do
  bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes $c --workloads c --writers-list 1 \
    --client-hosts "$HOSTS" --trial 1 --duration 300 --run-tag $TAG-10g-fix
done
step "workload a at 5 GiB 300 s; linearizable a 300 s + c 600 s at 5 GiB"
bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes 5368709120 --workloads a --writers-list 1 \
  --client-hosts "$HOSTS" --trial 1 --duration 300 --run-tag $TAG-10g-fix
bash $RUN --log-trim --linearizable --record-cnt $RC --lru-cache-bytes 5368709120 --workloads a --writers-list 1 \
  --client-hosts "$HOSTS" --trial 1 --duration 300 --run-tag $TAG-10g-fix
bash $RUN --log-trim --linearizable --record-cnt $RC --lru-cache-bytes 5368709120 --workloads c --writers-list 1 \
  --client-hosts "$HOSTS" --trial 1 --duration 600 --run-tag $TAG-10g-fix

step "scaling: workload a at 5 GiB, 2 and 4 client nodes, 120 s"
for n in 2 4; do
  bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes 5368709120 --workloads a --writers-list 1 \
    --client-hosts "$(hosts_n $n)" --trial 1 --duration 120 --run-tag $TAG-10g-fix-scale
done
step "JNI control: workload a at 5 GiB, 300 s"
bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes 5368709120 --corfu-client jni --workloads a --writers-list 1 \
  --client-hosts "$HOSTS" --trial 1 --duration 300 --run-tag $TAG-10g-fix-jni

step "tier cells behind 80 MiB RAM: 2.5 GiB c 300 s, 5 GiB c 600 s + a 600 s, 20 GiB a c 1800 s"
run_tier10() {
  bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes 83886080 --disk-cache-bytes $1 --workloads "$2" \
    --writers-list 1 --client-hosts "$HOSTS" --trial 1 --duration $3 --run-tag $TAG-10g-fix-tier
}
run_tier10 2684354560  c     300
run_tier10 5368709120  "c a" 600
run_tier10 21474836480 "a c" 1800
ssh -o BatchMode=yes "$SRV" 'pkill -KILL -f "org.corfudb.infrastructure.[C]orfuServer" || true; df -h /tank/ssd | tail -1'
step "CHAIN-DONE"
