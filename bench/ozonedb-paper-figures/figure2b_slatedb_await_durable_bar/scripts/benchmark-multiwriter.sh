#!/usr/bin/env bash

set -eu # stop on errors and undefined variables

# Run the SlateDB multi-writer benchmark and collect the results in a CSV file.
# For each trial the script runs the slatedb-bencher multi-writer command for 60 s with
# the given writer count, concurrency, put percentage and flush interval. It writes the
# log of each run to target/bencher/results/logs and appends the final statistics to the
# results CSV. Set SLATEDB_BENCH_AWAIT_DURABLE to add --await-durable to each run. Set
# SLATEDB_BENCH_CLEAN to start each run from an empty path. The script reads the AWS
# credentials from .env_aws if that file exists.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export RUST_LOG=${RUST_LOG:-bencher=info}
OUT="target/bencher/results"

if [ -f ".env_aws" ]; then
  set -a
  source ".env_aws"
  set +a
fi

mkdir -p $OUT/logs

run_bench() {
  local num_writers="$1"
  local concurrency="$2"
  local put_percentage="$3"
  local num_keys="$4"
  local log_file="$5"
  local flush_interval_ms="$6"
  local trial="$7"

  local clean_flag=""
  if [ -n "${SLATEDB_BENCH_CLEAN:-}" ]; then
    clean_flag="--clean"
  fi

  local durable_flag=""
  if [ -n "${SLATEDB_BENCH_AWAIT_DURABLE:-}" ]; then
    durable_flag="--await-durable"
  fi

  local s3_path="/slatedb-bencher-mw_${num_writers}w_${concurrency}c_${put_percentage}p_${flush_interval_ms}ms_t${trial}"

  local bench_cmd="cargo run -r --package slatedb-bencher -- \
    --path $s3_path $clean_flag multi-writer \
    --duration 60 \
    --val-len 8192 \
    --block-cache-size 100663296 \
    --meta-cache-size 33554432 \
    --put-percentage $put_percentage \
    --concurrency $concurrency \
    --num-writers $num_writers \
    --key-count $num_keys \
    --reopen-on-fence \
    --reopen-delay-ms 100 \
    $durable_flag \
  "

  $bench_cmd | tee "$log_file"

  # mc rm --recursive --force "myminio/${AWS_BUCKET}${s3_path}"
}

# Extract the final stats from a log file. Append them as a CSV row to the results file.
append_final_stats() {
  local num_writers="$1"
  local concurrency="$2"
  local put_percentage="$3"
  local flush_interval_ms="$4"
  local log_file="$5"
  local csv_file="$6"

  # sed knows only \1 to \9, so the twelve fields come out of two passes over
  # the line: nine rates and counts, then the three counters at the end.
  local line rates counters
  line=$(grep "multi-writer final" "$log_file" | tail -1)
  rates=$(echo "$line" | sed -E 's/.*elapsed: ([0-9.]+)s.*put\/s: ([0-9.]+) \(([0-9.]+) MiB\/s\).*effective put\/s: ([0-9.]+) \(([0-9.]+) MiB\/s\).*get\/s: ([0-9.]+) \(([0-9.]+) MiB\/s\).*puts=([0-9]+).*effective_puts=([0-9]+).*/\1,\2,\3,\4,\5,\6,\7,\8,\9/')
  counters=$(echo "$line" | sed -E 's/.*gets=([0-9]+).*fences=([0-9]+).*fenced-before-flush=([0-9]+).*/\1,\2,\3/')
  echo "$num_writers,$concurrency,$put_percentage,$flush_interval_ms,$rates,$counters" >>"$csv_file"
}

# If CLOUD_PROVIDER is not set, set it to local.
export CLOUD_PROVIDER=${CLOUD_PROVIDER:-local}
echo "Using cloud provider: $CLOUD_PROVIDER"

# If CLOUD_PROVIDER is local and LOCAL_PATH is not set, set LOCAL_PATH.
if [ "$CLOUD_PROVIDER" = "local" ]; then
  export LOCAL_PATH=${LOCAL_PATH:-/tmp/slatedb}
  mkdir -p $LOCAL_PATH
  echo "Using local path: $LOCAL_PATH"
fi

CSV_FILE="$OUT/results.csv"
echo "num_writers,concurrency,put_percentage,flush_interval_ms,elapsed_s,puts_per_s,puts_mib_per_s,effective_puts_per_s,effective_puts_mib_per_s,gets_per_s,gets_mib_per_s,total_puts,effective_puts,total_gets,total_fences,fenced_before_flush" >"$CSV_FILE"

for trial in $(seq 1 10); do
  echo "=== Trial $trial/10 ==="
  for num_writers in 1 2 4 8 16 32; do
    for put_percentage in 100; do
      for flush_interval_ms in 5 10 20 40 60 100; do
        export SLATEDB_FLUSH_INTERVAL="${flush_interval_ms}ms"
        log_file="$OUT/logs/${num_writers}w_${put_percentage}p_${flush_interval_ms}ms_t${trial}.log"
        num_keys=$((put_percentage * 1000))

        run_bench "$num_writers" 1 "$put_percentage" "$num_keys" "$log_file" "$flush_interval_ms" "$trial"
        append_final_stats "$num_writers" 1 "$put_percentage" "$flush_interval_ms" "$log_file" "$CSV_FILE"
      done
    done
  done
done
