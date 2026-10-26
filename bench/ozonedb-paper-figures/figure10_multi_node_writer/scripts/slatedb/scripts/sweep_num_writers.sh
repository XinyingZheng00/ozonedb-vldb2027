#!/usr/bin/env bash
set -euo pipefail

# Sweep the YCSB workloads (a,b,c,d,f) and --num-writers for the run phase.
# Each iteration:
#   1. clears the run bucket path
#   2. copies the pre-loaded baseline from the load bucket path
#   3. runs the ycsb run phase against the fresh copy
#   4. clears the run bucket path again when the full sweep finishes
#
# Workloads a-f share the same load schema, so one pre-loaded baseline
# works for all of them. The script skips workload e (scans) by default,
# because it needs a different validation path.
#
# Prerequisite: the load phase must already have run into
# $MC_BASE/$LOAD_PREFIX (for example cargo run --release -- ... load -P workloads/workloada).

MC=${MC:-mc}
MC_BASE=${MC_BASE:-minio/slatedb-ycsb}
LOAD_PREFIX=${LOAD_PREFIX:-load}
RUN_PREFIX=${RUN_PREFIX:-run}

WORKLOAD_DIR=${WORKLOAD_DIR:-workloads}
WORKLOADS_LIST=${WORKLOADS_LIST:-"a b c d f"}
ENV_FILE=${ENV_FILE:-.env_minio}
DB_OPTIONS=${DB_OPTIONS:-SlateDb.toml}
OUTPUT_DIR=${OUTPUT_DIR:-results}
RUN_STAMP=${RUN_STAMP:-$(date +%Y%m%d-%H%M%S)}
SWEEP_OUTPUT_DIR="${OUTPUT_DIR}/${RUN_STAMP}"
DURATION=${DURATION:-300}
WRITERS_LIST=${WRITERS_LIST:-"2 4 8 16 32"}
AWAIT_DURABLE=${AWAIT_DURABLE:-0}
REOPEN_DELAY=${REOPEN_DELAY:-100}
TRIALS=${TRIALS:-1}

await_durable_args=()
case "${AWAIT_DURABLE,,}" in
1 | true | yes | on) await_durable_args=(--await-durable) ;;
esac

load_mc_path="${MC_BASE}/${LOAD_PREFIX}"
run_mc_path="${MC_BASE}/${RUN_PREFIX}"

clear_run_bucket() {
  echo "--- clearing ${run_mc_path} ---"
  "$MC" rm --recursive --versions --incomplete --force "${run_mc_path}/" >/dev/null 2>&1 || true
}

seed_run_bucket() {
  echo "--- mirroring ${load_mc_path} → ${run_mc_path} ---"
  "$MC" mirror --overwrite --remove "${load_mc_path}/" "${run_mc_path}/"
}

if ! "$MC" ls "${load_mc_path}/" >/dev/null 2>&1; then
  echo "error: load bucket ${load_mc_path} is empty or missing — run the load phase first" >&2
  exit 1
fi

mkdir -p "$SWEEP_OUTPUT_DIR"
echo "writing results to ${SWEEP_OUTPUT_DIR}"

for t in $(seq 1 "$TRIALS"); do
  for w in $WORKLOADS_LIST; do
    workload_path="${WORKLOAD_DIR}/workload${w}"
    if [[ ! -f "$workload_path" ]]; then
      echo "error: workload file ${workload_path} not found" >&2
      exit 1
    fi

    for n in $WRITERS_LIST; do
      echo "=========================================="
      echo "=== trial=${t} workload=${w} num_writers=${n}"
      echo "=========================================="

      clear_run_bucket
      seed_run_bucket

      cargo run --release -- \
        --env-file "$ENV_FILE" \
        --path "/run" \
        --db-options-path "$DB_OPTIONS" \
        --output-dir "$SWEEP_OUTPUT_DIR" \
        --trial "${t}" \
        run \
        -P "$workload_path" \
        --duration "$DURATION" \
        --num-writers "$n" \
        --reopen-on-fence \
        --reopen-delay-ms "$REOPEN_DELAY" \
        "${await_durable_args[@]}"
    done
  done
done

clear_run_bucket
echo "sweep complete"
