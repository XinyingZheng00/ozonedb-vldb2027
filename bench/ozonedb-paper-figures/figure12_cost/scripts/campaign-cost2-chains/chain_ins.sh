#!/bin/bash
# Edited for the artifact: ${NODE_A} ${NODE_B} ${NODE_C} (user@host) and
# ${NODE_A_HOST} ${NODE_B_HOST} ${NODE_C_HOST} replace the CloudLab login
# and the three node hostnames. OZONEDB_HOME comes from the
# environment. Everything else is as run.
# One cell: workload ai (50 % read, 50 % blind insert) at 100 GB, 100 MiB RAM, 300 s,
# on the probe-counter build. Twin of the workload-a diag cell of the same tag. That
# cell pays a read-modify-write per update. This chain first pushes
# ycsb/workloads/workloadai to the clients. No rebuild is needed: the file is data,
# not code. Runs detached on the laptop.
set -euo pipefail
trap 'echo "[chain $(date "+%F %T")] CHAIN-FAILED rc=$? line $LINENO"' ERR
export OZONEDB_HOME=${OZONEDB_HOME:?set OZONEDB_HOME to the ozonedb checkout}
export ANSIBLE_CONFIG=$OZONEDB_HOME/bench/ansible/ansible.cfg
cd "$OZONEDB_HOME"
TAG=cost2-20260828
SRV=${NODE_A}
HOSTS=$(python3 bench/scripts/ycsb_config.py --list clients --field ssh | paste -s -d, -)
step() { echo "[chain $(date '+%F %T')] $*"; }
R=bench/results/local

step "guard: server clear"
ssh -o BatchMode=yes "$SRV" 'pkill -KILL -f "org.corfudb.infrastructure.[C]orfuServer" || true; sleep 1; if pgrep -af "[C]assandraDaemon|[C]orfuServer"; then echo "server busy"; exit 1; fi; echo "server clear"; du -sh /mnt/corfu/load'

step "push workloadai to the clients"
(cd bench/ansible && ansible-playbook sync.yml 2>&1 | tail -3)
(cd bench/ansible && ansible -m shell -a 'ls ~/ozonedb/ycsb/workloads/workloadai' clients 2>&1 | grep -c workloadai)

step "cell: workload ai at 100 GB, 100 MiB RAM, 300 s (tag $TAG-100g-diag)"
bash bench/scripts/local/run_multinode_ycsb_with_corfu.sh --log-trim --record-cnt 100000000 \
  --lru-cache-bytes 104857600 --workloads ai --writers-list 1 --client-hosts "$HOSTS" \
  --trial 1 --duration 300 --run-tag $TAG-100g-diag

step "probe counters, writer 0"
f=$(ls $R/$TAG-100g-diag/1KB-999999999-100000000-workloadai-*lru100m_w0of8_t1_trial1.result | head -1)
awk '/\[lru_cache\] (sstable|levels)/' "$f" | cut -c1-400
awk '/\[READ\], Operations|\[INSERT\], Operations|Return=OK|Return=ERROR/' "$f" | head -6
ssh -o BatchMode=yes "$SRV" 'pkill -KILL -f "org.corfudb.infrastructure.[C]orfuServer" || true'
step "CHAIN-DONE"
