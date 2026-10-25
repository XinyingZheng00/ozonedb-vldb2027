#!/bin/bash
# Edited for publication: the CloudLab login and the server hostname were replaced by
# ${NODE_B} (user@host) and ${SSH_USER}. OZONEDB_HOME comes from the environment.
# Everything else is as run.
# PLAN-strict-100g: one trial of the strict frontier at 100 GB.
# Usage: chain_trial.sh <trial number>. The script runs detached on the laptop.
# Cassandra runs first, then OzoneDB. They share the server and must never
# run at the same time.
set -euo pipefail
trap 'echo "[chain $(date "+%F %T")] CHAIN-FAILED rc=$? line $LINENO"' ERR
T=${1:?trial number (1, 2 or 3)}

export OZONEDB_HOME=${OZONEDB_HOME:?set OZONEDB_HOME to the ozonedb checkout}
cd "$OZONEDB_HOME"

TAG=strict100-20260830
SRV=${NODE_B}
RC=100000000
DUR=300
CACHE=17179869184          # 16 GiB LRU block cache per writer
TIER=53687091200           # 50 GiB disk-cache tier per writer
WORKLOADS="a b c f d"      # d runs last: it is the only workload that inserts keys
POINTS="2:1 4:1 8:1 8:2 8:4"   # hosts:writers_per_host, which gives 2 4 8 16 32 writers
CASS=bench/scripts/local/run_multinode_ycsb_with_cassandra.sh
OZ=bench/scripts/local/run_multinode_ycsb_with_corfu.sh

HOSTS=$(python3 bench/scripts/ycsb_config.py --list clients --field ssh | paste -s -d, -)
hosts_n() { echo "$HOSTS" | tr ',' '\n' | head -n "$1" | paste -s -d, -; }
step() { echo "[chain $(date '+%F %T')] $*"; }

step "trial $T: guard -- nothing else may hold the server"
ssh -o BatchMode=yes "$SRV" '/tank/cassandra/cassandra_ctl.sh stop >/dev/null 2>&1 || true; pkill -KILL -f "org.corfudb.infrastructure.[C]orfuServer" || true; sleep 2; if pgrep -af "[C]assandraDaemon|[C]orfuServer"; then echo "server busy"; exit 1; fi; echo "server clear"; df -h /tank/ssd | tail -1'

step "trial $T: Cassandra serial, 25 cells, ${DUR} s each"
for point in $POINTS; do
  n=${point%%:*}; w=${point##*:}
  step "  cassandra point ${n}x${w}"
  bash $CASS --consistency serial --record-cnt $RC \
    --workloads "$WORKLOADS" --writers-list "$w" --client-hosts "$(hosts_n "$n")" \
    --trial "$T" --duration $DUR --run-tag $TAG
done
ssh -o BatchMode=yes "$SRV" '/tank/cassandra/cassandra_ctl.sh stop; df -h /tank/ssd | tail -1'

step "trial $T: OzoneDB linearizable, 16 GiB cache + 50 GiB tier, 25 cells, ${DUR} s each"
for point in $POINTS; do
  n=${point%%:*}; w=${point##*:}
  step "  ozonedb point ${n}x${w}"
  bash $OZ --linearizable --log-trim --record-cnt $RC \
    --lru-cache-bytes $CACHE --disk-cache-bytes $TIER --disk-cache-dir /tank/cache \
    --workloads "$WORKLOADS" --writers-list "$w" --client-hosts "$(hosts_n "$n")" \
    --trial "$T" --duration $DUR --run-tag $TAG
done
ssh -o BatchMode=yes "$SRV" "pkill -KILL -f 'org.corfudb.infrastructure.[C]orfuServer' || true; df -h /tank/ssd | tail -1"

step "trial $T: client tier space after the run"
for h in $(echo "$HOSTS" | tr ',' ' '); do
  echo -n "$h: "
  ssh -n -o BatchMode=yes -o ConnectTimeout=8 "${SSH_USER}@$h" 'df -h --output=avail /tank/cache | tail -1'
done

step "CHAIN-DONE trial $T"
