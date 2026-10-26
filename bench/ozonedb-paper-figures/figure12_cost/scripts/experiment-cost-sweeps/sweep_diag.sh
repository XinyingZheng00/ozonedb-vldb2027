#!/bin/bash
# Diagnose the extra SSTable fetches on workload a (1.35 fetches per read at 100 GB,
# against 1.02 on workload c).
# The script runs one workload-a cell and one workload-c cell at 100 GB on the build with
# the probe counters. Then it prints the per-level probes/pruned/absent lines of writer 0.
# First it moves the 100 GB snapshot back into /mnt/corfu/load and the 10 GB snapshot
# into load-10g.
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

step "guard: server clear, counters build on the clients"
ssh -o BatchMode=yes "$SRV" 'pkill -KILL -f "org.corfudb.infrastructure.[C]orfuServer" || true; sleep 1; if pgrep -af "[C]assandraDaemon|[C]orfuServer"; then echo "server busy"; exit 1; fi; echo "server clear"; ls /mnt/corfu/'
ssh -o BatchMode=yes "$C0" 'grep -c noteProbe ~/ozonedb/src/db/sstable/table_reader.cpp; ls -la --time-style=+%T ~/ozonedb/build/libOzoneDB.so'

step "swap snapshots: 10 GB -> load-10g, 100 GB -> load"
ssh -o BatchMode=yes "$SRV" 'set -e; cd /mnt/corfu; rm -rf load-10g load-bucket-10g; mv load load-10g; mv load-bucket load-bucket-10g; mv load-100g load; mv load-bucket-100g load-bucket; du -sh load load-bucket'

step "diag cells at 100 GB, 100 MiB RAM: workload a 300 s, workload c 300 s (tag $TAG-100g-diag)"
bash $RUN --log-trim --record-cnt $RC --lru-cache-bytes 104857600 --workloads "a c" --writers-list 1 \
  --client-hosts "$HOSTS" --trial 1 --duration 300 --run-tag $TAG-100g-diag

step "probe counters, writer 0 of each cell"
for wl in a c; do
  f=$(ls $R/$TAG-100g-diag/1KB-999999999-${RC}-workload${wl}-*lru100m_w0of8_t1_trial1.result | head -1)
  echo "== $wl: $f"
  awk '/\[lru_cache\] (sstable|levels)/' "$f" | cut -c1-400
  awk '/\[READ\], Operations|\[READ\], Return=/' "$f"
done
ssh -o BatchMode=yes "$SRV" 'pkill -KILL -f "org.corfudb.infrastructure.[C]orfuServer" || true'
step "SWEEP-DONE"
