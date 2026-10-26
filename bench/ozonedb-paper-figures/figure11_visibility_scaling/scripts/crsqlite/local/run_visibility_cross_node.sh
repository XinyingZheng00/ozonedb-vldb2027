#!/usr/bin/env bash
#
# Cross-node cr-sqlite visibility experiment. W writer replicas and their
# sync daemons run on clients[0]. The reader replica runs ALONE on
# clients[1]. The anti-entropy mesh runs over the real LAN (the daemons
# dial cloudlab.lan_hosts) instead of localhost. The ack channel is TCP
# with a go/done handshake (see bench.py visibility-writer/-reader). The
# miss count is clock-free, and the latency is referenced to the
# reader-side notify receipt. This mirrors ozonedb's
# run_visibility_cross_node.sh, so the two engines' cross-node runs are
# comparable parameter for parameter.
#
#   bash run_visibility_cross_node.sh [--read-barrier] [--num-writers 8]
#        [--interval-ms 50] [--rate 20] [--duration 15] [--value-size 1000]
#        [--poll-ms 1] [--key-timeout-s 30]
#
# The script is laptop-driven, because the client nodes cannot ssh each
# other. It starts the reader role on clients[1], runs the writer role on
# clients[0], and pulls BOTH nodes' output dirs (the home dirs are NOT
# shared).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cfg() { python3 "$CONFIG_DIR/ycsb_config.py" "$@"; }

READ_BARRIER=""
WRITERS=1
INTERVAL_MS=""
RATE=20
DURATION=15
VALUE_SIZE=1000
POLL_MS=1
KEY_TIMEOUT_S=30
PORT="${VISX_PORT:-7912}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --read-barrier) READ_BARRIER="--read-barrier"; shift ;;
    --num-writers) WRITERS="$2"; shift 2 ;;
    --interval-ms) INTERVAL_MS="$2"; shift 2 ;;
    --rate) RATE="$2"; shift 2 ;;
    --duration) DURATION="$2"; shift 2 ;;
    --value-size) VALUE_SIZE="$2"; shift 2 ;;
    --poll-ms) POLL_MS="$2"; shift 2 ;;
    --key-timeout-s) KEY_TIMEOUT_S="$2"; shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

SSH_USER="$(cfg --get nodes.ssh_user)"
SSH_KEY="$(cfg --get nodes.ssh_private_key_path)"
SSH_KEY="${SSH_KEY/#\~/$HOME}"
W_SSH="$(cfg --get cloudlab.hosts | python3 -c 'import ast,sys; print(ast.literal_eval(sys.stdin.read())[0])')"
R_SSH="$(cfg --get cloudlab.hosts | python3 -c 'import ast,sys; print(ast.literal_eval(sys.stdin.read())[1])')"
R_LAN="$(cfg --get cloudlab.lan_hosts | python3 -c 'import ast,sys; print(ast.literal_eval(sys.stdin.read())[1])')"

SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i "$SSH_KEY")

TAG="$(date +%Y%m%d-%H%M%S)"
OUT="bench/results/consistency/visibility-xnode-$TAG"
LOCAL_OUT="$CONFIG_DIR/../results/consistency/visibility-xnode-$TAG"
mkdir -p "$LOCAL_OUT"

INTERVAL_ARG=""
[[ -n "$INTERVAL_MS" ]] && INTERVAL_ARG="--interval-ms $INTERVAL_MS"

READER_TIMEOUT=$((DURATION + KEY_TIMEOUT_S + 420))
echo "[visx-cr] starting reader on $R_SSH (listen $R_LAN:$PORT, $WRITERS writers)"
ssh "${SSH_OPTS[@]}" "$SSH_USER@$R_SSH" \
  "bash -lc 'cd \$CRSQLITE_HOME && timeout $READER_TIMEOUT python3 bench/scripts/bench.py visibility-reader --out-dir $OUT --port $PORT --num-writers $WRITERS --rate $RATE --duration $DURATION --value-size $VALUE_SIZE --poll-ms $POLL_MS --key-timeout-s $KEY_TIMEOUT_S --run-timeout-s $((DURATION + KEY_TIMEOUT_S + 120)) $INTERVAL_ARG $READ_BARRIER'" \
  > "$LOCAL_OUT/reader-ssh.log" 2>&1 &
READER_PID=$!

echo "[visx-cr] running $WRITERS writer(s) on $W_SSH (connect $R_LAN:$PORT)"
ssh "${SSH_OPTS[@]}" "$SSH_USER@$W_SSH" \
  "bash -lc 'cd \$CRSQLITE_HOME && timeout $((DURATION + 420)) python3 bench/scripts/bench.py visibility-writer --out-dir $OUT --connect $R_LAN:$PORT --num-writers $WRITERS --rate $RATE --duration $DURATION --value-size $VALUE_SIZE $INTERVAL_ARG'" \
  | tee "$LOCAL_OUT/writer-ssh.log"

echo "[visx-cr] waiting for the reader to drain"
if ! wait "$READER_PID"; then
  echo "[visx-cr] reader exited nonzero -- see $LOCAL_OUT/reader-ssh.log" >&2
  exit 1
fi

echo "[visx-cr] pulling both nodes' outputs"
rsync -a -e "ssh ${SSH_OPTS[*]}" "$SSH_USER@$W_SSH:crsqlite/$OUT/" "$LOCAL_OUT/"
rsync -a -e "ssh ${SSH_OPTS[*]}" "$SSH_USER@$R_SSH:crsqlite/$OUT/" "$LOCAL_OUT/"
echo "[visx-cr] results in $LOCAL_OUT"
