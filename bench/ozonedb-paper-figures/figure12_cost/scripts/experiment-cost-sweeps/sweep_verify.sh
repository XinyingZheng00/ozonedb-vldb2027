#!/bin/bash
# Run the storage tests against a fresh Corfu server. Then run the Cassandra 10 GB load
# and two quorum control cells.
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
ssh_srv() { ssh -o BatchMode=yes "$SRV" "$@"; }
ssh_c0() { ssh -o BatchMode=yes "$C0" "$@"; }

step "guard: nothing on the server"
ssh_srv 'pkill -KILL -f "org.corfudb.infrastructure.[C]orfuServer" || true; sleep 1; if pgrep -af "[C]assandraDaemon|[C]orfuServer"; then echo "server busy"; exit 1; fi; echo "server clear"'

step "build runUnitTests"
ssh_c0 "bash -lc 'cd ~/ozonedb && cmake --build build --target runUnitTests corfu_native_probe -j\$(nproc) 2>&1 | tail -3'"

step "fresh empty Corfu on the server (/mnt/corfu/test)"
ssh_srv 'cd ~/CorfuDB && rm -rf /mnt/corfu/test && mkdir -p /mnt/corfu/test && ( setsid nohup env CORFUDB_HEAP=122880 ./bin/corfu_server -l /mnt/corfu/test -s -a 10.10.1.1 9090 </dev/null >/tmp/corfu_test.log 2>&1 & ); for i in $(seq 1 60); do ss -ltn | grep -q ":9090 " && break; sleep 1; done; ss -ltn | grep ":9090 " || { tail -20 /tmp/corfu_test.log; exit 1; }'

step "CorfuStorageTest + DiskCacheStorageTest + FrequencySketch on native"
ssh_c0 "bash -lc 'cd ~/ozonedb/build && CORFU_TEST_CLIENT=native CORFU_TEST_ENDPOINT=10.10.1.1:9090 ./runUnitTests --gtest_filter=\"CorfuStorageTest.*:DiskCacheStorageTest.*:FrequencySketch*\" 2>&1 | grep -E \"^\[  (PASSED|FAILED)|tests? from|SKIPPED|FAILED TEST\" | tail -8'"

step "stop the test Corfu, drop its log dir"
ssh_srv 'pkill -KILL -f "org.corfudb.infrastructure.[C]orfuServer" || true; sleep 2; rm -rf /mnt/corfu/test; ls -la /mnt/corfu/'

step "Cassandra: 10 GB load, 8 writers on the load host"
bash bench/scripts/local/load_multinode_cassandra.sh --writers 8 --record-cnt 10000000
ssh_srv '/tank/cassandra/cassandra_ctl.sh space; rm -rf /tank/cassandra/data.load-10g; cp -a /tank/cassandra/data.load /tank/cassandra/data.load-10g; du -sh /tank/cassandra/data.load-10g; df -h /tank/ssd | tail -1'

step "Cassandra: control cells quorum a c, 120 s, tag $TAG-cass-10g-rerun"
bash bench/scripts/local/run_multinode_ycsb_with_cassandra.sh --consistency quorum --record-cnt 10000000 \
  --workloads "a c" --writers-list 1 --client-hosts "$HOSTS" --trial 1 --duration 120 --run-tag $TAG-cass-10g-rerun
ssh_srv '/tank/cassandra/cassandra_ctl.sh stop || true; df -h /tank/ssd | tail -1'
step "SWEEP-DONE"
