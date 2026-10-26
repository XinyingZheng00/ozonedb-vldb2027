#!/bin/bash
# Bootstrap the cluster for the cost experiment.
# The script installs the tree and builds OzoneKV on every node. Then it installs
# Cassandra on the server node (NODE_A).
# The script expects this disk layout: /tank/ssd on the server, with the MinIO bind
# mount and the /mnt/corfu and /tank/cassandra links. Every client has /tank/cache.
# Before you start the script, set these variables:
#   - OZONEDB_HOME: the root of this repository
#   - NODE_A, NODE_B, NODE_C: the SSH login (user@host) of each cluster node
#   - NODE_A_HOST, NODE_B_HOST, NODE_C_HOST: the hostname of each cluster node
# The script writes its log to bench/results/local/cost-sweeps/.
set -euo pipefail
trap 'echo "[sweep $(date "+%F %T")] SWEEP-FAILED rc=$? line $LINENO"' ERR
export OZONEDB_HOME=${OZONEDB_HOME:?set OZONEDB_HOME to the ozonedb checkout}
export ANSIBLE_CONFIG=$OZONEDB_HOME/bench/ansible/ansible.cfg
cd "$OZONEDB_HOME/bench/ansible"
SRV=${NODE_A}
step() { echo "[sweep $(date '+%F %T')] $*"; }

step "bootstrap: push tree + setup.sh on all 9 nodes (clients: client; server: corfu-server + minio)"
ansible-playbook bootstrap.yml -e target=nodes -e setup_timeout=10800
step "cassandra role on the server"
ssh -o BatchMode=yes "$SRV" "bash -lc 'cd ~/ozonedb && bash bench/scripts/setup.sh --role cassandra' 2>&1 | tail -20"
step "post-check: build artefacts, tier mounts, server services"
ansible -m shell -a "bash -lc 'ls -la ~/ozonedb/build/libOzoneDB.so ~/ozonedb/build/libozonedb.so ~/ozonedb/build/corfu_native_probe 2>&1 | tail -3; mountpoint /tank/cache; git -C ~/ozonedb log --oneline -1 2>/dev/null || echo no-git'" clients
ssh -o BatchMode=yes "$SRV" "bash -lc 'systemctl is-active minio; mc ls ozonedb-local/ | head; ls -ld /mnt/corfu/load /mnt/corfu/run_batch /tank/cassandra/current /tank/cassandra/cassandra_ctl.sh; df -h /tank/ssd | tail -1'"
step "SWEEP-DONE"
