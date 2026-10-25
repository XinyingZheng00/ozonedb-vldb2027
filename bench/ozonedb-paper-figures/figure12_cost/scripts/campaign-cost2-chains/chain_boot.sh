#!/bin/bash
# Edited for the artifact: ${NODE_A} ${NODE_B} ${NODE_C} (user@host) and
# ${NODE_A_HOST} ${NODE_B_HOST} ${NODE_C_HOST} replace the CloudLab login
# and the three node hostnames. OZONEDB_HOME comes from the
# environment. Everything else is as run.
# New experiment 2026-08-29 (ozonedb-cass, amd197 and amd189..amd200). This chain
# bootstraps every node from the worktree, then runs the cassandra role on the server.
# The SSD layout was done by hand before this chain (layout.py in the job tmp dir).
# The layout: /tank/ssd on the server with the MinIO bind mount and the /mnt/corfu and
# /tank/cassandra links. Every client has /tank/cache.
set -euo pipefail
trap 'echo "[chain $(date "+%F %T")] CHAIN-FAILED rc=$? line $LINENO"' ERR
export OZONEDB_HOME=${OZONEDB_HOME:?set OZONEDB_HOME to the ozonedb checkout}
export ANSIBLE_CONFIG=$OZONEDB_HOME/bench/ansible/ansible.cfg
cd "$OZONEDB_HOME/bench/ansible"
SRV=${NODE_A}
step() { echo "[chain $(date '+%F %T')] $*"; }

step "bootstrap: push tree + setup.sh on all 9 nodes (clients: client; amd197: corfu-server + minio)"
ansible-playbook bootstrap.yml -e target=nodes -e setup_timeout=10800
step "cassandra role on the server"
ssh -o BatchMode=yes "$SRV" "bash -lc 'cd ~/ozonedb && bash bench/scripts/setup.sh --role cassandra' 2>&1 | tail -20"
step "post-check: build artefacts, tier mounts, server services"
ansible -m shell -a "bash -lc 'ls -la ~/ozonedb/build/libOzoneDB.so ~/ozonedb/build/libozonedb.so ~/ozonedb/build/corfu_native_probe 2>&1 | tail -3; mountpoint /tank/cache; git -C ~/ozonedb log --oneline -1 2>/dev/null || echo no-git'" clients
ssh -o BatchMode=yes "$SRV" "bash -lc 'systemctl is-active minio; mc ls ozonedb-local/ | head; ls -ld /mnt/corfu/load /mnt/corfu/run_batch /tank/cassandra/current /tank/cassandra/cassandra_ctl.sh; df -h /tank/ssd | tail -1'"
step "CHAIN-DONE"
