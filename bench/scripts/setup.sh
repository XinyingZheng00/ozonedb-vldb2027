#!/usr/bin/env bash
#
# One entrypoint for provisioning a node. Idempotent: re-running is cheap and
# never duplicates shell config or reinstalls what is already present.
#
#   bash bench/scripts/setup.sh --role client        # bench/YCSB machine
#   bash bench/scripts/setup.sh --role corfu-server  # the shared-log node
#   bash bench/scripts/setup.sh --role minio         # SSTable object store
#   bash bench/scripts/setup.sh --role cassandra     # baseline server (nodes.cassandra)
#   bash bench/scripts/setup.sh --role all           # client + corfu-server + minio
#
# You do NOT need to source this. It writes ~/.ozonedb.env and wires a guarded
# block into ~/.profile and ~/.bashrc, then sources that file itself for the
# rest of the run. Open a new shell (or `. ~/.ozonedb.env`) afterwards.
#
# JDK policy. The client needs a JDK only for the YCSB driver and the JNI
# shim (libozonedb.so); the engine talks to CorfuDB with its own C++ client.
# The corfu-server role builds CorfuDB, which wants a recent JDK. Hence two
# knobs: --jdk (default 25) is the node's persistent JAVA_HOME, --corfu-jdk
# (default 25) is used only for the CorfuDB build on the log node. javac 25
# still accepts YCSB's -source 8 with warnings.
#
# vcpkg is NOT vendored. The client role clones it into $REPO_ROOT/vcpkg
# (gitignored) at the builtin-baseline pinned in vcpkg.json, and CMake finds
# it there (or through $VCPKG_ROOT).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

ROLES=()
JDK_VERSION="25"
CORFU_JDK_VERSION="25"
JDK_FALLBACKS=(25 21 17 11)
CORFU_JDK_FALLBACKS=(25 21 17)
DO_BUILD=1
VCPKG_REPO="https://github.com/microsoft/vcpkg.git"
VCPKG_DIR="${VCPKG_ROOT:-$REPO_ROOT/vcpkg}"
CORFU_REPO="https://github.com/CorfuDB/CorfuDB.git"
CORFU_CLONE_DIR="${CLONE_DIR:-$HOME/CorfuDB}"
# The CorfuDB commit the paper ran against (0.9.1.0-SNAPSHOT).
# The native client speaks this server's wire protocol; a newer server may
# differ.
CORFU_COMMIT="8f144d4c92535dfe5fad8e1a5c9ddaba5b7ad8d5"
CORFU_MVN_VERSION="0.9.1.0-SNAPSHOT"
CORFU_DATA_DIR="/mnt/corfu"
TANK_DIR="/tank"
MINIO_DATA_DIR="/tank/minio"
MINIO_BUCKET=""
MINIO_USER="minioadmin"
MINIO_PASSWORD="minioadmin"
MINIO_PORT=9000
MINIO_CONSOLE_PORT=9001
# Cassandra 5.0 runs on JDK 11 or 17 only, and bin/cassandra refuses anything
# else -- so the baseline server gets its own JDK, separate from the node's
# JDK 25 (which CorfuDB's bytecode needs). It is only referenced from
# $CASSANDRA_INSTALL_DIR/env.sh, never from ~/.ozonedb.env.
CASSANDRA_JDK_VERSION="17"
CASSANDRA_JDK_FALLBACKS=(17 11)
CASSANDRA_INSTALL_DIR="" # default: cassandra.install_dir from ycsb.yaml
CASSANDRA_BIND=""        # default: the nodes.cassandra[].lan that is a local address
CASSANDRA_VERSION=""     # default: cassandra.version from ycsb.yaml
CASSANDRA_MIRROR="https://archive.apache.org/dist/cassandra"

ENV_FILE="$HOME/.ozonedb.env"
MARK_BEGIN="# >>> ozonedb env >>>"
MARK_END="# <<< ozonedb env <<<"

usage() {
  cat <<EOF
Provision a node for OzoneDB. Idempotent -- re-running is cheap and never
duplicates shell config or reinstalls what is already present.

  bash bench/scripts/setup.sh --role client        # bench/YCSB machine
  bash bench/scripts/setup.sh --role corfu-server  # the shared-log node
  bash bench/scripts/setup.sh --role minio         # SSTable object store
  bash bench/scripts/setup.sh --role cassandra     # baseline server (nodes.cassandra)
  bash bench/scripts/setup.sh --role all           # client + corfu-server + minio

You do NOT need to source this. It writes ~/.ozonedb.env and wires a guarded
block into ~/.profile and ~/.bashrc. Open a new shell afterwards, or run
\`. ~/.ozonedb.env\`.

The cassandra role installs the Apache tarball under cassandra.install_dir
(ycsb.yaml), binds it to this node's nodes.cassandra lan address, and
installs bench/scripts/cassandra_ctl.sh next to it. It does not start the
server: bench/scripts/local/load_multinode_cassandra.sh and
run_multinode_ycsb_with_cassandra.sh do that per phase. It is not part of
--role all because it shares the box with corfu-server by default.

JDK policy: the client needs a JDK for YCSB and the JNI shim only (--jdk,
the node's persistent JAVA_HOME). The corfu-server role builds CorfuDB with
--corfu-jdk. Both fall back through a candidate list if the exact apt package
is unavailable.

The client role clones vcpkg into \$VCPKG_ROOT (default: $VCPKG_DIR,
gitignored) at the baseline pinned in vcpkg.json and bootstraps it. The
first build then compiles the C++ dependencies (aws-sdk-cpp takes the
longest).

Options:
  --role ROLE          client | corfu-server | minio | cassandra | all
                       (repeatable, default: client)
  --jdk N              persistent JAVA_HOME JDK major version (default: ${JDK_FALLBACKS[0]})
  --corfu-jdk N        JDK used only to build CorfuDB (default: ${CORFU_JDK_FALLBACKS[0]})
  --no-build           client: skip bench/scripts/build.sh at the end
  --vcpkg-dir PATH     where to clone vcpkg (default: $VCPKG_DIR)
  --corfu-dir PATH     CorfuDB checkout (default: $CORFU_CLONE_DIR)
  --corfu-data PATH    corfu-server log directories (default: $CORFU_DATA_DIR)
  --tank PATH          data directory the configs point db_path at (default: $TANK_DIR)
  --minio-data PATH    minio data directory (default: $MINIO_DATA_DIR)
  --bucket NAME        minio bucket (default: s3.bucket from ycsb.yaml)
  --cassandra-jdk N    JDK for the Cassandra server only (default: ${CASSANDRA_JDK_FALLBACKS[0]};
                       Cassandra 5.0 accepts 11 or 17)
  --cassandra-version V  Apache Cassandra release (default: cassandra.version from ycsb.yaml)
  --cassandra-dir PATH install dir (default: cassandra.install_dir from ycsb.yaml)
  --cassandra-bind ADDR  listen/rpc address (default: the nodes.cassandra lan
                       address that belongs to this machine)
  -h | --help
EOF
}

log() { printf '\033[1;36m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[setup:warn]\033[0m %s\n' "$*" >&2; }
die() {
  printf '\033[1;31m[setup:error]\033[0m %s\n' "$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
  --role)
    ROLES+=("$2")
    shift 2
    ;;
  --jdk)
    JDK_VERSION="$2"
    shift 2
    ;;
  --corfu-jdk)
    CORFU_JDK_VERSION="$2"
    shift 2
    ;;
  --no-build)
    DO_BUILD=0
    shift
    ;;
  --vcpkg-dir)
    VCPKG_DIR="$2"
    shift 2
    ;;
  --corfu-dir)
    CORFU_CLONE_DIR="$2"
    shift 2
    ;;
  --corfu-data)
    CORFU_DATA_DIR="$2"
    shift 2
    ;;
  --tank)
    TANK_DIR="$2"
    shift 2
    ;;
  --minio-data)
    MINIO_DATA_DIR="$2"
    shift 2
    ;;
  --bucket)
    MINIO_BUCKET="$2"
    shift 2
    ;;
  --cassandra-jdk)
    CASSANDRA_JDK_VERSION="$2"
    shift 2
    ;;
  --cassandra-version)
    CASSANDRA_VERSION="$2"
    shift 2
    ;;
  --cassandra-dir)
    CASSANDRA_INSTALL_DIR="$2"
    shift 2
    ;;
  --cassandra-bind)
    CASSANDRA_BIND="$2"
    shift 2
    ;;
  -h | --help)
    usage
    exit 0
    ;;
  *) die "unknown option: $1 (try --help)" ;;
  esac
done

[[ ${#ROLES[@]} -eq 0 ]] && ROLES=(client)
if [[ " ${ROLES[*]} " == *" all "* ]]; then
  ROLES=(client corfu-server minio)
fi
for r in "${ROLES[@]}"; do
  case "$r" in
  client | corfu-server | minio | cassandra) ;;
  *) die "unknown role: $r" ;;
  esac
done

# ---------------------------------------------------------------- primitives

APT_UPDATED=0
apt_update_once() {
  [[ $APT_UPDATED -eq 1 ]] && return 0
  log "apt-get update"
  sudo apt-get update -qq
  APT_UPDATED=1
}

# Install only what is missing, so a re-run costs nothing.
apt_install() {
  local missing=()
  for pkg in "$@"; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
  done
  if [[ ${#missing[@]} -eq 0 ]]; then
    log "already installed: $*"
    return 0
  fi
  apt_update_once
  log "installing: ${missing[*]}"
  sudo apt-get install -y "${missing[@]}"
}

apt_available() { apt-cache show "$1" >/dev/null 2>&1; }

# Resolve JAVA_HOME for an installed JDK major version.
java_home_for() {
  local v="$1" arch candidate
  arch="$(dpkg --print-architecture)"
  for candidate in \
    "/usr/lib/jvm/java-${v}-openjdk-${arch}" \
    "/usr/lib/jvm/java-1.${v}.0-openjdk-${arch}"; do
    [[ -x "$candidate/bin/javac" ]] && {
      echo "$candidate"
      return 0
    }
  done
  # Glob fallback: packaging names vary across Ubuntu releases.
  for candidate in /usr/lib/jvm/java-${v}-openjdk*; do
    [[ -x "$candidate/bin/javac" ]] && {
      echo "$candidate"
      return 0
    }
  done
  return 1
}

# Install the first available JDK from a candidate list; echo its major version.
# $1 = explicitly requested version (may be empty), remaining args = fallbacks.
ensure_jdk() {
  local requested="$1"
  shift
  local candidates=("$@")
  [[ -n "$requested" ]] && candidates=("$requested")

  local v
  for v in "${candidates[@]}"; do
    if java_home_for "$v" >/dev/null; then
      echo "$v"
      return 0
    fi
  done

  # This function's stdout IS its return value (callers use "$(ensure_jdk ...)"),
  # so everything chatty must go to stderr. Letting apt print to stdout here
  # corrupted the captured version string and killed the script silently via
  # set -e on fresh nodes -- the "dies right after installing a JDK" bug.
  apt_update_once >&2
  for v in "${candidates[@]}"; do
    if apt_available "openjdk-${v}-jdk"; then
      log "installing openjdk-${v}-jdk" >&2
      sudo apt-get install -y "openjdk-${v}-jdk" >&2
      java_home_for "$v" >/dev/null && {
        echo "$v"
        return 0
      }
    fi
  done

  if [[ -n "$requested" ]]; then
    die "openjdk-${requested}-jdk is not installable on this release. Pass a different --jdk."
  fi
  die "none of these JDKs are installable: ${candidates[*]}"
}

# ------------------------------------------------------------------ env file

strip_env_block() {
  local rc="$1"
  [[ -f "$rc" ]] || return 0
  awk -v b="$MARK_BEGIN" -v e="$MARK_END" '
    $0 == b { skip = 1 }
    !skip   { print }
    $0 == e { skip = 0 }
  ' "$rc" >"${rc}.ozonedb.tmp" && mv "${rc}.ozonedb.tmp" "$rc"
}

# Rewrites, never appends -- the old setup_node.sh grew a duplicate set of
# exports on every run.
wire_env() {
  local java_home="$1"

  # $JAVA_HOME and $PATH are escaped so they expand when the file is sourced.
  # The old script expanded $JAVA_HOME at write time, while it was still
  # unset, and wrote a literal `export PATH=/bin:$PATH` into both rc files.
  # The PATH guard keeps repeated sourcing idempotent: a bash login shell runs
  # .profile, which on Ubuntu sources .bashrc, so this file gets sourced twice.
  cat >"$ENV_FILE" <<EOF
# Generated by bench/scripts/setup.sh -- do not edit, changes are overwritten.
export OZONEDB_HOME="$REPO_ROOT"
export JAVA_HOME="$java_home"
case ":\$PATH:" in
  *":\$JAVA_HOME/bin:"*) ;;
  *) export PATH="\$JAVA_HOME/bin:\$PATH" ;;
esac
EOF
  log "wrote $ENV_FILE"

  local rc
  for rc in "$HOME/.profile" "$HOME/.bashrc"; do
    [[ -f "$rc" ]] || touch "$rc"
    strip_env_block "$rc"
    {
      echo "$MARK_BEGIN"
      echo "[ -f \"$ENV_FILE\" ] && . \"$ENV_FILE\""
      echo "$MARK_END"
    } >>"$rc"
    log "wired $ENV_FILE into $rc"
  done

  # `source ~/.bashrc` from a script is a no-op: Ubuntu's default .bashrc
  # returns immediately for non-interactive shells. Source the env file itself.
  # shellcheck disable=SC1090
  . "$ENV_FILE"
}

# -------------------------------------------------------------------- vcpkg

# Clone vcpkg at the builtin-baseline in vcpkg.json and bootstrap the CLI.
# Idempotent: an existing checkout is fetched and moved to the baseline.
vcpkg_baseline() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["builtin-baseline"])' "$REPO_ROOT/vcpkg.json"
}

install_vcpkg() {
  local baseline
  baseline="$(vcpkg_baseline)"
  [[ -n "$baseline" ]] || die "vcpkg.json has no builtin-baseline"
  if [[ ! -d "$VCPKG_DIR/.git" ]]; then
    log "cloning vcpkg into $VCPKG_DIR"
    git clone "$VCPKG_REPO" "$VCPKG_DIR"
  else
    log "reusing existing vcpkg checkout at $VCPKG_DIR"
  fi
  log "pinning vcpkg to $baseline"
  (cd "$VCPKG_DIR" &&
    { git cat-file -e "$baseline^{commit}" 2>/dev/null || git fetch origin; } &&
    git checkout -q "$baseline")
  if [[ ! -x "$VCPKG_DIR/vcpkg" ]]; then
    log "bootstrapping vcpkg"
    (cd "$VCPKG_DIR" && ./bootstrap-vcpkg.sh -disableMetrics)
  fi
  # CMake finds a vcpkg/ inside the repo on its own; any other location
  # needs VCPKG_ROOT in the environment.
  if [[ "$VCPKG_DIR" != "$REPO_ROOT/vcpkg" ]]; then
    export VCPKG_ROOT="$VCPKG_DIR"
  fi
}

# --------------------------------------------------------------- corfu server

# Clone and build CorfuDB at the pinned commit. Only the corfu-server role
# needs this: the engine's own C++ client speaks to the server over TCP.
corfu_runtime_present() {
  [[ -f "$HOME/.m2/repository/org/corfudb/runtime/$CORFU_MVN_VERSION/runtime-$CORFU_MVN_VERSION.jar" ]]
}

install_corfu_runtime() {
  if corfu_runtime_present; then
    log "CorfuDB $CORFU_MVN_VERSION already built -- skipping (delete ~/.m2/repository/org/corfudb to force a rebuild)"
    return 0
  fi

  local corfu_jdk corfu_java_home
  corfu_jdk="$(ensure_jdk "$CORFU_JDK_VERSION" "${CORFU_JDK_FALLBACKS[@]}")"
  corfu_java_home="$(java_home_for "$corfu_jdk")"
  log "building CorfuDB with JDK $corfu_jdk ($corfu_java_home)"

  if [[ ! -d "$CORFU_CLONE_DIR/.git" ]]; then
    log "cloning CorfuDB into $CORFU_CLONE_DIR"
    git clone "$CORFU_REPO" "$CORFU_CLONE_DIR"
  else
    log "reusing existing CorfuDB checkout at $CORFU_CLONE_DIR"
  fi
  log "pinning CorfuDB to $CORFU_COMMIT ($CORFU_MVN_VERSION)"
  (cd "$CORFU_CLONE_DIR" &&
    { git cat-file -e "$CORFU_COMMIT^{commit}" 2>/dev/null || git fetch origin; } &&
    git checkout -q "$CORFU_COMMIT")

  # The tail of the reactor (integration-test modules like `it`) does not
  # always compile; everything we consume -- the shaded infrastructure jar
  # bin/corfu_server runs -- builds earlier, so a late reactor failure is
  # survivable as long as the runtime actually landed.
  if ! (cd "$CORFU_CLONE_DIR" && JAVA_HOME="$corfu_java_home" mvn -q clean install -DskipTests); then
    corfu_runtime_present ||
      die "CorfuDB build failed before installing org.corfudb:runtime into ~/.m2"
    warn "CorfuDB reactor failed after the runtime was installed (flaky integration-test modules at HEAD) -- continuing"
  fi
  log "CorfuDB built"
}

# ------------------------------------------------------------------- roles

role_client() {
  log "=== role: client ==="

  # `time` is GNU time: load_local_ycsb_multiproc.py wraps every YCSB writer
  # in /usr/bin/time -v so client CPU and peak RSS land in the .result file.
  apt_install cmake maven python3-pip zip pkg-config build-essential git ninja-build time

  local jdk java_home
  jdk="$(ensure_jdk "$JDK_VERSION" "${JDK_FALLBACKS[@]}")"
  java_home="$(java_home_for "$jdk")"
  log "node JAVA_HOME -> $java_home (JDK $jdk)"

  wire_env "$java_home"

  # Ubuntu 24.04 marks the system python externally-managed (PEP 668), so
  # `pip install --user` refuses to run. The bench needs are small enough to
  # come from apt instead; requirements.txt stays as documentation and for
  # anyone running the harness inside a venv.
  log "installing python requirements (via apt; PEP 668 blocks pip --user)"
  apt_install python3-yaml python3-matplotlib python3-numpy

  log "ensuring $TANK_DIR exists and is writable"
  sudo mkdir -p "$TANK_DIR"
  sudo chmod 777 "$TANK_DIR"

  install_vcpkg

  if [[ $DO_BUILD -eq 1 ]]; then
    log "building ozonedb"
    bash "$SCRIPT_DIR/build.sh"
  else
    log "--no-build: skipping build.sh"
  fi

  java -version 2>&1 | sed 's/^/[setup]   /'
}

role_corfu_server() {
  log "=== role: corfu-server ==="

  # sysstat: pidstat for bench/scripts/server_sampler.sh (server CPU per cell).
  apt_install maven git sysstat curl

  install_corfu_runtime

  # run_ycsb_with_corfu.sh resets run_batch from load on every server start:
  #   rm -rf $CORFU_DATA_DIR/run_batch && cp -r $CORFU_DATA_DIR/load $CORFU_DATA_DIR/run_batch
  # Both must exist; nothing created them before this script.
  log "creating $CORFU_DATA_DIR/{load,run_batch}"
  sudo mkdir -p "$CORFU_DATA_DIR/load" "$CORFU_DATA_DIR/run_batch"
  sudo chmod -R 777 "$CORFU_DATA_DIR"

  log "corfu-server ready. Start it with:"
  log "  cd $CORFU_CLONE_DIR && ./bin/corfu_server -l $CORFU_DATA_DIR/run_batch -s -a <host> 9090"
}

role_minio() {
  log "=== role: minio ==="

  # The corfu base config defaults sstable_backend to s3, so the default
  # benchmark path needs an object store.
  local bucket="$MINIO_BUCKET"
  if [[ -z "$bucket" ]]; then
    bucket="$(python3 - "$REPO_ROOT/bench/scripts/config/ycsb.yaml" <<'PY' 2>/dev/null || true
import sys
try:
    import yaml
    with open(sys.argv[1]) as f:
        print((yaml.safe_load(f).get("s3") or {}).get("bucket", ""))
except Exception:
    print("")
PY
    )"
  fi
  [[ -z "$bucket" ]] && bucket="ozonedb-ycsb"

  # sysstat + curl: bench/scripts/server_sampler.sh reads the MinIO metrics
  # endpoint and runs pidstat on the minio process.
  apt_install sysstat curl

  if ! command -v minio >/dev/null 2>&1; then
    log "installing minio server"
    sudo curl -fsSL -o /usr/local/bin/minio https://dl.min.io/server/minio/release/linux-amd64/minio
    sudo chmod +x /usr/local/bin/minio
  else
    log "minio already installed"
  fi
  if ! command -v mc >/dev/null 2>&1; then
    log "installing minio client"
    sudo curl -fsSL -o /usr/local/bin/mc https://dl.min.io/client/mc/release/linux-amd64/mc
    sudo chmod +x /usr/local/bin/mc
  else
    log "mc already installed"
  fi

  sudo mkdir -p "$MINIO_DATA_DIR"
  sudo chmod 777 "$MINIO_DATA_DIR"

  log "writing systemd unit /etc/systemd/system/minio.service"
  sudo tee /etc/systemd/system/minio.service >/dev/null <<EOF
[Unit]
Description=MinIO (ozonedb sstable store)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
Environment=MINIO_ROOT_USER=$MINIO_USER
Environment=MINIO_ROOT_PASSWORD=$MINIO_PASSWORD
# /minio/v2/metrics/{cluster,bucket} without a bearer token, so
# bench/scripts/server_sampler.sh can read the S3 request counters that the
# cost model is built on. Bench LAN only.
Environment=MINIO_PROMETHEUS_AUTH_TYPE=public
ExecStart=/usr/local/bin/minio server $MINIO_DATA_DIR --address :$MINIO_PORT --console-address :$MINIO_CONSOLE_PORT
Restart=always
RestartSec=3
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  # restart, not `enable --now`: a running minio keeps its old environment
  # (the metrics auth setting above) until it is restarted.
  sudo systemctl enable minio >/dev/null 2>&1 || true
  sudo systemctl restart minio

  log "waiting for minio on 127.0.0.1:$MINIO_PORT"
  local i
  for i in $(seq 1 30); do
    (exec 3<>/dev/tcp/127.0.0.1/"$MINIO_PORT") >/dev/null 2>&1 && break
    sleep 1
  done

  mc alias set ozonedb-local "http://127.0.0.1:$MINIO_PORT" "$MINIO_USER" "$MINIO_PASSWORD" >/dev/null
  if mc ls "ozonedb-local/$bucket" >/dev/null 2>&1; then
    log "bucket '$bucket' already exists"
  else
    log "creating bucket '$bucket'"
    mc mb "ozonedb-local/$bucket"
  fi

  log "minio ready. Point s3.endpoint in ycsb.yaml at http://<this-host>:$MINIO_PORT"
}

# ycsb.yaml reader for the cassandra role. Everything that decides how the
# server is configured lives in the `cassandra:` block, so a value never has
# to be repeated on the command line.
ycsb_cfg() {
  python3 "$SCRIPT_DIR/ycsb_config.py" --config "$REPO_ROOT/bench/scripts/config/ycsb.yaml" "$@"
}

# Rewrite one top-level cassandra.yaml key in place, uncommenting it if the
# stock file ships it commented out. Dies if the key is not left exactly once
# -- an upstream layout change must fail loudly, not bind localhost.
cassandra_yaml_set() {
  local file="$1" key="$2" value="$3"
  sed -i -E "s|^#? ?${key}:.*|${key}: ${value}|" "$file"
  local n
  n="$(grep -cE "^${key}:" "$file" || true)"
  [[ "$n" -eq 1 ]] || die "cassandra.yaml: expected exactly one '${key}:' line after rewrite, found $n"
}

role_cassandra() {
  log "=== role: cassandra ==="

  # pyyaml is for ycsb_config.py on this node. The CQL client is NOT cqlsh:
  # Cassandra 5.0's cqlsh refuses Python >= 3.12 (its driver imports asyncore,
  # removed in 3.12) and the image ships only 3.12, so we build a venv with
  # cassandra-driver and drive CQL through bench/scripts/cassandra_cql.py.
  # libev-dev + build-essential + python3-dev let pip build the libev reactor,
  # which is the one that actually connects on 3.12 (asyncio times out).
  # sysstat: pidstat for bench/scripts/server_sampler.sh.
  apt_install curl tar python3 python3-yaml python3-venv build-essential python3-dev libev-dev sysstat

  local version install_dir port heap new_heap
  version="${CASSANDRA_VERSION:-$(ycsb_cfg --get cassandra.version)}" || die "cassandra.version missing from ycsb.yaml"
  install_dir="${CASSANDRA_INSTALL_DIR:-$(ycsb_cfg --get cassandra.install_dir)}" || die "cassandra.install_dir missing"
  port="$(ycsb_cfg --get cassandra.port)"
  heap="$(ycsb_cfg --get cassandra.heap)"
  new_heap="$(ycsb_cfg --get cassandra.new_heap)"

  # Which nodes.cassandra entry is this machine? Match its lan address against
  # the local interfaces. Falls back to --cassandra-bind for the odd host.
  local -a lan_addrs
  read -r -a lan_addrs <<<"$(ycsb_cfg --list cassandra --field lan | tr '\n' ' ')"
  [[ ${#lan_addrs[@]} -gt 0 ]] || die "nodes.cassandra (or nodes.log) resolves to no addresses"
  local bind="$CASSANDRA_BIND" local_ips a
  if [[ -z "$bind" ]]; then
    local_ips=" $(hostname -I 2>/dev/null || true) "
    for a in "${lan_addrs[@]}"; do
      if [[ "$local_ips" == *" $a "* ]]; then
        bind="$a"
        break
      fi
    done
  fi
  [[ -n "$bind" ]] || die "none of nodes.cassandra's lan addresses (${lan_addrs[*]}) is on this machine. Pass --cassandra-bind ADDR."
  local seeds=""
  for a in "${lan_addrs[@]}"; do
    seeds="${seeds:+$seeds,}$a:7000"
  done
  log "bind=$bind seeds=$seeds version=$version install_dir=$install_dir"

  local jdk java_home
  jdk="$(ensure_jdk "$CASSANDRA_JDK_VERSION" "${CASSANDRA_JDK_FALLBACKS[@]}")"
  java_home="$(java_home_for "$jdk")"
  log "cassandra JAVA_HOME -> $java_home (JDK $jdk)"

  sudo mkdir -p "$install_dir"
  sudo chmod 777 "$install_dir"

  local home="$install_dir/apache-cassandra-$version"
  if [[ -x "$home/bin/cassandra" ]]; then
    log "cassandra $version already unpacked at $home"
  else
    local tarball="$install_dir/apache-cassandra-$version-bin.tar.gz"
    log "downloading apache-cassandra-$version from $CASSANDRA_MIRROR"
    curl -fsSL -o "$tarball" "$CASSANDRA_MIRROR/$version/apache-cassandra-$version-bin.tar.gz"
    tar -xzf "$tarball" -C "$install_dir"
    rm -f "$tarball"
    [[ -x "$home/bin/cassandra" ]] || die "unpack did not produce $home/bin/cassandra"
  fi
  ln -sfn "$home" "$install_dir/current"

  # Configure from the pristine file every time, so a re-run never stacks
  # edits. The data directories all sit under $install_dir/data so that
  # cassandra_ctl.sh can wipe/snapshot/restore one tree.
  local conf="$home/conf/cassandra.yaml" data="$install_dir/data"
  [[ -f "$conf.dist" ]] || cp "$conf" "$conf.dist"
  cp "$conf.dist" "$conf"
  cassandra_yaml_set "$conf" cluster_name "'ozonedb-bench'"
  cassandra_yaml_set "$conf" listen_address "$bind"
  cassandra_yaml_set "$conf" rpc_address "$bind"
  cassandra_yaml_set "$conf" native_transport_port "$port"
  cassandra_yaml_set "$conf" commitlog_directory "$data/commitlog"
  cassandra_yaml_set "$conf" hints_directory "$data/hints"
  cassandra_yaml_set "$conf" saved_caches_directory "$data/saved_caches"
  # data_file_directories is a list; the stock file has it commented out
  # as two lines. Replace the key line and let the ctl script own the dir.
  sed -i -E "s|^#? ?data_file_directories:.*|data_file_directories:\n    - $data/data|" "$conf"
  [[ "$(grep -cE '^data_file_directories:' "$conf")" -eq 1 ]] || die "cassandra.yaml: data_file_directories rewrite failed"
  sed -i -E "s|^( *)- seeds:.*|\1- seeds: \"$seeds\"|" "$conf"
  grep -qE "^ *- seeds: \"$seeds\"" "$conf" || die "cassandra.yaml: seeds rewrite failed"
  mkdir -p "$data" "$install_dir/logs"

  # Everything cassandra_ctl.sh needs, in one sourced file. Deliberately NOT
  # ~/.ozonedb.env: the node's own JAVA_HOME must stay on JDK 25 for Corfu.
  cat >"$install_dir/env.sh" <<EOF
# Generated by bench/scripts/setup.sh --role cassandra -- do not edit.
export JAVA_HOME="$java_home"
export CASSANDRA_HOME="$install_dir/current"
export CASSANDRA_CONF="$install_dir/current/conf"
export CASSANDRA_LOG_DIR="$install_dir/logs"
export CASSANDRA_BIND="$bind"
export CASSANDRA_CQL_PORT="$port"
export MAX_HEAP_SIZE="$heap"
export HEAP_NEWSIZE="$new_heap"
export CASSANDRA_CQL_PYTHON="$install_dir/pyenv/bin/python"
export CASSANDRA_CQL_HELPER="$install_dir/cassandra_cql.py"
export PATH="\$JAVA_HOME/bin:\$CASSANDRA_HOME/bin:\$PATH"
EOF
  log "wrote $install_dir/env.sh"

  install -m 0755 "$SCRIPT_DIR/cassandra_ctl.sh" "$install_dir/cassandra_ctl.sh"
  install -m 0644 "$SCRIPT_DIR/cassandra_cql.py" "$install_dir/cassandra_cql.py"
  log "installed $install_dir/cassandra_ctl.sh + cassandra_cql.py"

  # The CQL client venv (see the apt note above). Idempotent: reuse an existing
  # venv that already imports the driver, else build it.
  local venv="$install_dir/pyenv"
  if "$venv/bin/python" -c "import cassandra.io.libevreactor" >/dev/null 2>&1; then
    log "cassandra-driver venv already present at $venv"
  else
    log "building cassandra-driver venv at $venv (libev reactor)"
    python3 -m venv "$venv"
    "$venv/bin/pip" -q install --upgrade pip
    "$venv/bin/pip" -q install "cassandra-driver==3.29.1"
    "$venv/bin/python" -c "import cassandra.io.libevreactor" ||
      die "cassandra-driver venv did not build the libev reactor (is libev-dev installed?)"
  fi

  # Cassandra warns (and under load, fails mmap) below this; idempotent.
  if [[ "$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)" -lt 1048575 ]]; then
    log "raising vm.max_map_count to 1048575"
    sudo sysctl -q -w vm.max_map_count=1048575
    echo "vm.max_map_count=1048575" | sudo tee /etc/sysctl.d/99-cassandra.conf >/dev/null
  fi

  log "cassandra $version ready (not started). Control it with:"
  log "  $install_dir/cassandra_ctl.sh start|stop|status|wait|wipe|schema|save-load|restore-load"
  log "or from the orchestrator: bench/scripts/local/load_multinode_cassandra.sh"
}

# -------------------------------------------------------------------- main

log "repo: $REPO_ROOT"
log "roles: ${ROLES[*]}"

for role in "${ROLES[@]}"; do
  case "$role" in
  client) role_client ;;
  corfu-server) role_corfu_server ;;
  minio) role_minio ;;
  cassandra) role_cassandra ;;
  esac
done

log "done. Open a new shell, or run: . $ENV_FILE"
