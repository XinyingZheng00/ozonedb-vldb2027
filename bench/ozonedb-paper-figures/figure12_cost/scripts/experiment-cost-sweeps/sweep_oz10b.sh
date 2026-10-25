#!/bin/bash
# Measure the OzoneKV cells at 10 GB:
#   - one 16-writer load
#   - the h sweep
#   - workload a
#   - linearizable reads
#   - scaling
#   - the JNI control
#   - the key-space zipfian pairs
#   - the tier cells
# The script is the same as sweep_oz10.sh without the zipfian check. A failed cell
# stops the script.
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
hosts_n() { echo "$HOSTS" | tr ',' '\n' | head -n "$1" | paste -s -d, -; }
step() { echo "[sweep $(date '+%F %T')] $*"; }
RUN=bench/scripts/local/run_multinode_ycsb_with_corfu.sh
RC=10000000
SWEEPS=bench/results/local/cost-sweeps

step "guard: nothing else on the server"
ssh -o BatchMode=yes "$SRV" '/tank/cassandra/cassandra_ctl.sh stop >/dev/null 2>&1 || true; pkill -KILL -f "org.corfudb.infrastructure.[C]orfuServer" || true; sleep 1; if pgrep -af "[C]assandraDaemon"; then echo "server busy"; exit 1; fi; echo "server clear"; ls -d /mnt/corfu/load /mnt/corfu/load-bucket; df -h /tank/ssd | tail -1'

step "h sweep, workload c, 5 GiB and 640 MiB at 600 s"
for c in 5368709120 671088640; do
  bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes $c --workloads c --writers-list 1 \
    --client-hosts "$HOSTS" --trial 1 --duration 600 --run-tag $TAG-10g
done
step "h sweep, workload c, 80 MiB / 10 MiB / 1.25 MiB at 300 s"
for c in 83886080 10485760 1310720; do
  bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes $c --workloads c --writers-list 1 \
    --client-hosts "$HOSTS" --trial 1 --duration 300 --run-tag $TAG-10g
done
step "workload a at 5 GiB and 80 MiB, 600 s"
for c in 5368709120 83886080; do
  bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes $c --workloads a --writers-list 1 \
    --client-hosts "$HOSTS" --trial 1 --duration 600 --run-tag $TAG-10g
done
step "linearizable a c at 5 GiB, 120 s"
bash $RUN --log-trim --linearizable --record-cnt $RC --lru-cache-bytes 5368709120 --workloads "a c" --writers-list 1 \
  --client-hosts "$HOSTS" --trial 1 --duration 120 --run-tag $TAG-10g
step "server scaling, workload a on 2 and 4 hosts, 120 s"
for n in 2 4; do
  bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes 5368709120 --workloads a --writers-list 1 \
    --client-hosts "$(hosts_n $n)" --trial 1 --duration 120 --run-tag $TAG-scale
done
step "JNI control, workload a at 5 GiB, 600 s"
bash $RUN --log-trim --corfu-client jni --record-cnt $RC --lru-cache-bytes 5368709120 --workloads a --writers-list 1 \
  --client-hosts "$HOSTS" --trial 1 --duration 600 --run-tag $TAG-jni

step "10 GB key-space zipfian twins at 80 MiB and 1.25 MiB, 300 s"
for c in 83886080 1310720; do
  bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes $c --workloads cz --writers-list 1 \
    --client-hosts "$HOSTS" --trial 1 --duration 300 --run-tag $TAG-10g-zipf
done

step "tier cells behind 80 MiB RAM: 2.5 GiB c 300 s, 5 GiB a c 600 s, 20 GiB a c 600 s"
run_tier() {
  bash $RUN --log-trim --record-cnt $1 --lru-cache-bytes 83886080 --disk-cache-bytes $2 --workloads "$3" \
    --writers-list 1 --client-hosts "$HOSTS" --trial 1 --duration $5 --run-tag $4
}
run_tier $RC 2684354560  c     $TAG-10g-tier 300
run_tier $RC 5368709120  "a c" $TAG-10g-tier 600
run_tier $RC 21474836480 "a c" $TAG-10g-tier 600

step "SWEEP-DONE"
