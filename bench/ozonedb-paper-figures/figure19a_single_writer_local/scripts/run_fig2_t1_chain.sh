#!/usr/bin/env bash
# Wait for the in-flight Fig 2 multi-writer sweep to exit. $1 is its PID.
# Then launch a follow-up sweep with --threads 1 for every (system, workload)
# pair. The scaling lines then have a single-thread baseline.
#
# The script can survive an SSH disconnect. The caller invokes it under setsid
# and nohup. It ignores stdin and redirects all output to a dedicated log.

set -uo pipefail

WAIT_PID="${1:?usage: $0 <pid_of_running_sweep>}"
# The T=1 baseline data goes into the fig1 dir. That is the paper's Fig 1, single-writer.
OZONEDB_HOME="${OZONEDB_HOME:-$HOME/ozonedb}"
LOG_DIR="$OZONEDB_HOME/bench/results/local/fig1-single-writer-local"
mkdir -p "$LOG_DIR"
CHAIN_LOG="$LOG_DIR/sweep_t1.log"

echo "[chain] $(date -u +%FT%TZ) waiting for pid $WAIT_PID ..." >> "$CHAIN_LOG"
while kill -0 "$WAIT_PID" 2>/dev/null; do
    sleep 30
done
echo "[chain] $(date -u +%FT%TZ) pid $WAIT_PID exited; launching T=1 sweep" >> "$CHAIN_LOG"

# Pause briefly, so that the OS can settle (drop_caches, and more).
sleep 10

OZONEDB_HOME="$OZONEDB_HOME" \
exec python3 "$OZONEDB_HOME"/bench/scripts/local/run_fig2_sweep.py \
    --systems ozonedb,trunkcpp,bcw2,hctree,rocksdb \
    --workloads a,b,c,d,f \
    --threads 1 \
    --repeats 1 \
    --duration 120 \
    --results-dir "$LOG_DIR" \
    >> "$CHAIN_LOG" 2>&1
