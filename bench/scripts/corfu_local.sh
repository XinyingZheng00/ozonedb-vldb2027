#!/usr/bin/env bash
#
# A single-node CorfuDB server on this machine, for the CorfuStorageTest.*
# suite and the corfu_smoke binaries. No apt, no sudo, no /mnt/corfu: the
# NixOS route to what `setup.sh --role corfu-server` does on Ubuntu.
#
#   bash bench/scripts/corfu_local.sh build   # clone + build CorfuDB at the pinned commit
#   bash bench/scripts/corfu_local.sh start   # fresh server (empty log dir) in the background
#   bash bench/scripts/corfu_local.sh stop
#   bash bench/scripts/corfu_local.sh test    # start fresh, CorfuStorageTest.*, stop
#
# On NixOS run it from `nix-shell corfu-server.nix`; it re-executes itself
# inside the `corfu-fhs` environment that file provides. Elsewhere it uses
# the java and mvn on PATH.
#
# Environment:
#   CORFU_CLONE_DIR  CorfuDB checkout   (default: ~/CorfuDB, as setup.sh)
#   CORFU_PORT       server port        (default: 9090)
#   CORFU_STATE_DIR  log dir, pid, log  (default: ${XDG_RUNTIME_DIR:-/tmp}/ozonedb-corfu)
#
# `start` always wipes the log dir: the Corfu tests need a fresh server,
# because they trim the log and fail-stop on a trimmed one.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [[ -z "${CORFU_FHS:-}" ]] && command -v corfu-fhs >/dev/null 2>&1; then
  exec corfu-fhs "$SCRIPT_DIR/corfu_local.sh" "$@"
fi

CORFU_REPO="https://github.com/CorfuDB/CorfuDB.git"
CORFU_CLONE_DIR="${CORFU_CLONE_DIR:-$HOME/CorfuDB}"
CORFU_PORT="${CORFU_PORT:-9090}"
CORFU_STATE_DIR="${CORFU_STATE_DIR:-${XDG_RUNTIME_DIR:-/tmp}/ozonedb-corfu}"
CORFU_LOG_DIR="$CORFU_STATE_DIR/log"
CORFU_PID_FILE="$CORFU_STATE_DIR/server.pid"
CORFU_OUT="$CORFU_STATE_DIR/server.out"

# setup.sh owns the pinned commit; read it from there rather than repeat it.
CORFU_COMMIT="$(sed -n 's/^CORFU_COMMIT="\([0-9a-f]*\)".*/\1/p' "$SCRIPT_DIR/setup.sh")"
[[ -n "$CORFU_COMMIT" ]] || {
  echo "cannot read CORFU_COMMIT from $SCRIPT_DIR/setup.sh" >&2
  exit 1
}

log() { printf '\033[1;36m[corfu]\033[0m %s\n' "$*"; }
die() {
  printf '\033[1;31m[corfu:error]\033[0m %s\n' "$*" >&2
  exit 1
}

shaded_jar() {
  compgen -G "$CORFU_CLONE_DIR/infrastructure/target/infrastructure-*-shaded.jar" | head -n1
}

cmd_build() {
  command -v mvn >/dev/null || die "mvn not on PATH (nix-shell corfu-server.nix)"
  if [[ ! -d "$CORFU_CLONE_DIR/.git" ]]; then
    log "cloning CorfuDB into $CORFU_CLONE_DIR"
    git clone "$CORFU_REPO" "$CORFU_CLONE_DIR"
  fi
  log "pinning CorfuDB to $CORFU_COMMIT"
  (cd "$CORFU_CLONE_DIR" &&
    { git cat-file -e "$CORFU_COMMIT^{commit}" 2>/dev/null || git fetch origin; } &&
    git checkout -q "$CORFU_COMMIT")

  log "building with $(java -version 2>&1 | head -n1)"
  # Same tolerance as setup.sh: the tail of the reactor (integration-test
  # modules) does not always compile, but the shaded server jar builds earlier.
  if ! (cd "$CORFU_CLONE_DIR" && mvn -q clean install -DskipTests); then
    [[ -n "$(shaded_jar)" ]] || die "CorfuDB build failed before the shaded server jar was built"
    log "reactor failed after the server jar was built -- continuing"
  fi
  log "built $(shaded_jar)"
}

server_pid() {
  [[ -f "$CORFU_PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$CORFU_PID_FILE")"
  kill -0 -- "-$pid" 2>/dev/null && echo "$pid"
}

cmd_stop() {
  local pid
  if pid="$(server_pid)"; then
    # bin/corfu_server runs java as a child rather than exec'ing it, so
    # signal the whole process group setsid gave it.
    log "stopping corfu_server (process group $pid)"
    kill -- "-$pid" 2>/dev/null || true
    for _ in $(seq 50); do
      kill -0 -- "-$pid" 2>/dev/null || break
      sleep 0.2
    done
    kill -0 -- "-$pid" 2>/dev/null && kill -9 -- "-$pid"
  fi
  rm -f "$CORFU_PID_FILE"
}

cmd_start() {
  [[ -n "$(shaded_jar)" ]] || die "no CorfuDB server jar under $CORFU_CLONE_DIR -- run '$0 build' first"
  cmd_stop
  if (exec 3<>"/dev/tcp/127.0.0.1/$CORFU_PORT") 2>/dev/null; then
    die "port $CORFU_PORT is already in use (set CORFU_PORT)"
  fi
  rm -rf "$CORFU_LOG_DIR"
  mkdir -p "$CORFU_LOG_DIR"

  log "starting corfu_server on 127.0.0.1:$CORFU_PORT (log dir $CORFU_LOG_DIR)"
  # setsid: the server outlives this script, and its pid is the process
  # group `stop` signals.
  setsid "$CORFU_CLONE_DIR/bin/corfu_server" -l "$CORFU_LOG_DIR" -s -a 127.0.0.1 "$CORFU_PORT" \
    >"$CORFU_OUT" 2>&1 </dev/null &
  echo $! >"$CORFU_PID_FILE"

  for _ in $(seq 120); do
    server_pid >/dev/null || die "corfu_server exited during startup; see $CORFU_OUT"
    if (exec 3<>"/dev/tcp/127.0.0.1/$CORFU_PORT") 2>/dev/null; then
      log "corfu_server up (pid $(server_pid), output in $CORFU_OUT)"
      log "endpoint: CORFU_TEST_ENDPOINT=127.0.0.1:$CORFU_PORT"
      return 0
    fi
    sleep 0.5
  done
  die "corfu_server did not open port $CORFU_PORT within 60s; see $CORFU_OUT"
}

cmd_test() {
  local bin="$REPO_ROOT/build/runUnitTests"
  [[ -x "$bin" ]] || die "$bin missing -- build with -DOZONEDB_ENABLE_CORFU=ON first"
  cmd_start
  trap cmd_stop EXIT
  local rc=0
  # Run from build/, as every test does. The Corfu tests keep their
  # checkpoints under $TMPDIR; OZONEDB_TEST_ROOT matters only if GTEST_FILTER
  # widens the run.
  (cd "$REPO_ROOT/build" &&
    CORFU_TEST_ENDPOINT="127.0.0.1:$CORFU_PORT" \
      OZONEDB_TEST_ROOT="${OZONEDB_TEST_ROOT:-$CORFU_STATE_DIR/test}" \
      ./runUnitTests --gtest_filter="${GTEST_FILTER:-CorfuStorageTest.*}") || rc=$?
  return "$rc"
}

case "${1:-}" in
  build) cmd_build ;;
  start) cmd_start ;;
  stop) cmd_stop ;;
  test) cmd_test ;;
  *)
    sed -n '3,22p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
