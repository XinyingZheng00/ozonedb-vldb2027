#!/bin/bash
# Measure one OzoneKV cell: workload ai (50 % read, 50 % blind insert) at 100 GB,
# 100 MiB RAM, 300 s, on the build with the probe counters.
# The cell is the pair of the workload-a cell of the same tag, which pays a
# read-modify-write per update.
# First the script copies ycsb/workloads/workloadai to the clients. A rebuild is not
# necessary: the file is data, not code.
# Before you start the script, set these variables:
#   - OZONEDB_HOME: the root of this repository
#   - NODE_A, NODE_B, NODE_C: the SSH login (user@host) of each cluster node
#   - NODE_A_HOST, NODE_B_HOST, NODE_C_HOST: the hostname of each cluster node
# The script writes its log to bench/results/local/cost-sweeps/.
set -euo pipefail
trap 'echo "[sweep $(date "+%F %T")] SWEEP-FAILED rc=$? line $LINENO"' ERR
export OZONEDB_HOME=${OZONEDB_HOME:?set OZONEDB_HOME to the ozonedb checkout}
export ANSIBLE_CONFIG=$OZONEDB_HOME/bench/ansible/ansible.cfg
cd "$OZONEDB_HOME"
TAG=cost-20260828
SRV=${NODE_A}
HOSTS=$(python3 bench/scripts/ycsb_config.py --list clients --field ssh | paste -s -d, -)
step() { echo "[sweep $(date '+%F %T')] $*"; }
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
step "SWEEP-DONE"
