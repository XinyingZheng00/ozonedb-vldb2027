#!/usr/bin/env bash
# Wait for the threads sweep with PID $1 to exit. Then run a second sweep with
# --threads 1 for every (system, workload) pair: the single-thread baseline of
# the scaling lines.
# The script ignores stdin and writes all output to its own log file, so it
# survives an SSH disconnect.

set -uo pipefail

WAIT_PID="${1:?usage: $0 <pid_of_running_sweep>}"
# T=1 baseline results go to the single-writer dir.
LOG_DIR="${OZONEDB_HOME:?OZONEDB_HOME is not set}/bench/results/local/fig1-single-writer-local"
mkdir -p "$LOG_DIR"
CHAIN_LOG="$LOG_DIR/sweep_t1.log"

echo "[chain] $(date -u +%FT%TZ) waiting for pid $WAIT_PID ..." >> "$CHAIN_LOG"
while kill -0 "$WAIT_PID" 2>/dev/null; do
    sleep 30
done
echo "[chain] $(date -u +%FT%TZ) pid $WAIT_PID exited; launching T=1 sweep" >> "$CHAIN_LOG"

# Let the OS settle.
sleep 10

exec python3 "$(dirname "$0")/run_fig2_sweep.py" \
    --systems ozonedb,trunkcpp,bcw2,hctree,rocksdb \
    --workloads a,b,c,d,f \
    --threads 1 \
    --repeats 1 \
    --duration 120 \
    --results-dir "$LOG_DIR" \
    >> "$CHAIN_LOG" 2>&1
