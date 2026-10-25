#!/usr/bin/env bash
#
# A MinIO server on this machine, for the S3StorageTest.* / S3DBTest.* suites
# and for configs whose sstable_backend is s3. No apt, no sudo, no systemd:
# the local equivalent of `setup.sh --role minio`.
#
#   bash bench/scripts/minio_local.sh start   # fresh data dir + bucket, in the background
#   bash bench/scripts/minio_local.sh stop
#   bash bench/scripts/minio_local.sh test    # start fresh, S3 tests, stop
#
# On NixOS run it from `nix-shell minio.nix`. Elsewhere it uses the minio and
# mc on PATH.
#
# Environment:
#   MINIO_PORT        S3 API port   (default: 9000, the s3_endpoint in src/config/corfu/*.json)
#   MINIO_BUCKET      bucket        (default: ozonedb-sstables, as shared_config_base.json)
#   MINIO_STATE_DIR   data, pid, log, mc config
#                     (default: ${XDG_RUNTIME_DIR:-/tmp}/ozonedb-minio)
#
# Credentials are minioadmin/minioadmin, as in the shipped configs. The server
# listens on 127.0.0.1 only.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

MINIO_PORT="${MINIO_PORT:-9000}"
MINIO_CONSOLE_PORT="${MINIO_CONSOLE_PORT:-$((MINIO_PORT + 1))}"
MINIO_BUCKET="${MINIO_BUCKET:-ozonedb-sstables}"
MINIO_STATE_DIR="${MINIO_STATE_DIR:-${XDG_RUNTIME_DIR:-/tmp}/ozonedb-minio}"
MINIO_DATA_DIR="$MINIO_STATE_DIR/data"
MINIO_PID_FILE="$MINIO_STATE_DIR/server.pid"
MINIO_OUT="$MINIO_STATE_DIR/server.out"
MINIO_USER="minioadmin"
MINIO_PASSWORD="minioadmin"
ENDPOINT="http://127.0.0.1:$MINIO_PORT"
# mc keeps its aliases here, not in ~/.mc.
export MC_CONFIG_DIR="$MINIO_STATE_DIR/mc"

log() { printf '\033[1;36m[minio]\033[0m %s\n' "$*"; }
die() {
  printf '\033[1;31m[minio:error]\033[0m %s\n' "$*" >&2
  exit 1
}

port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

server_pid() {
  [[ -f "$MINIO_PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$MINIO_PID_FILE")"
  kill -0 "$pid" 2>/dev/null && echo "$pid"
}

cmd_stop() {
  local pid
  if pid="$(server_pid)"; then
    log "stopping minio (pid $pid)"
    kill "$pid"
    for _ in $(seq 50); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.2
    done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
  fi
  rm -f "$MINIO_PID_FILE"
}

cmd_start() {
  command -v minio >/dev/null || die "minio not on PATH (nix-shell minio.nix)"
  command -v mc >/dev/null || die "mc not on PATH (nix-shell minio.nix)"
  cmd_stop
  local p
  for p in "$MINIO_PORT" "$MINIO_CONSOLE_PORT"; do
    port_open "$p" && die "port $p is already in use (set MINIO_PORT)"
  done
  rm -rf "$MINIO_DATA_DIR"
  mkdir -p "$MINIO_DATA_DIR" "$MC_CONFIG_DIR"

  log "starting minio on $ENDPOINT (data dir $MINIO_DATA_DIR)"
  # setsid: the server outlives this script, and `stop` signals it by pid.
  MINIO_ROOT_USER="$MINIO_USER" MINIO_ROOT_PASSWORD="$MINIO_PASSWORD" \
    MINIO_UPDATE=off MINIO_BROWSER=off \
    setsid minio server "$MINIO_DATA_DIR" \
    --address "127.0.0.1:$MINIO_PORT" --console-address "127.0.0.1:$MINIO_CONSOLE_PORT" \
    >"$MINIO_OUT" 2>&1 </dev/null &
  echo $! >"$MINIO_PID_FILE"

  # Ready once mc can authenticate; then create the bucket.
  for _ in $(seq 60); do
    server_pid >/dev/null || die "minio exited during startup; see $MINIO_OUT"
    if mc alias set ozonedb-local "$ENDPOINT" "$MINIO_USER" "$MINIO_PASSWORD" >/dev/null 2>&1; then
      mc mb --ignore-existing "ozonedb-local/$MINIO_BUCKET" >/dev/null
      log "minio up (pid $(server_pid), output in $MINIO_OUT), bucket '$MINIO_BUCKET'"
      log "endpoint: S3_TEST_ENDPOINT=$ENDPOINT"
      return 0
    fi
    sleep 0.5
  done
  die "minio did not come up within 30s; see $MINIO_OUT"
}

cmd_test() {
  local bin="$REPO_ROOT/build/runUnitTests"
  [[ -x "$bin" ]] || die "$bin missing -- build first (OZONEDB_ENABLE_S3 is on by default)"
  cmd_start
  trap cmd_stop EXIT
  local rc=0
  # AWS_EC2_METADATA_DISABLED: otherwise every S3Client first probes the EC2
  # metadata endpoint, which costs seconds off EC2.
  (cd "$REPO_ROOT/build" &&
    S3_TEST_ENDPOINT="$ENDPOINT" S3_TEST_BUCKET="$MINIO_BUCKET" AWS_EC2_METADATA_DISABLED=true \
      OZONEDB_TEST_ROOT="${OZONEDB_TEST_ROOT:-$MINIO_STATE_DIR/test}" \
      ./runUnitTests --gtest_filter="${GTEST_FILTER:-S3StorageTest.*:S3DBTest.*}") || rc=$?
  log "objects written: $(mc ls --recursive "ozonedb-local/$MINIO_BUCKET" | wc -l)"
  return "$rc"
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  test) cmd_test ;;
  *)
    sed -n '3,21p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
