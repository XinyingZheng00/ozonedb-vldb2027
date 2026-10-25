#!/bin/bash

# Runner mode. "threads" runs N threads in one process.
# "processes" runs N processes with 1 thread each.
RUNNER_MODE="${RUNNER_MODE:-threads}"

# Build the CSV file name from the current date and time.
CSV_FILE="results_$(date '+%Y%m%d_%H%M%S')_${RUNNER_MODE}.csv"

# Write the CSV header. The mode decides if "workers" means threads or processes.
echo "workers,avg_ops_sec,micros_op,p50_latency,p95_latency,p99_latency,time,open_time_ms" >"$CSV_FILE"

# The worker counts to test.
# 1 2 4 8 12 16 20 24 28 32 36 40 44 48 52 56 60 64
WORKER_COUNTS=(1 2 4 8 12 16 20 24 28 32 36 40 44 48 52 56 60 64)

echo "Creating DB at /tank/rocksdbtest/reopen"
mkdir -p /tank/rocksdbtest
./db_bench --db=/tank/rocksdbtest/reopen --benchmarks="filluniquerandom" --num=10000000 --seed=425

for W in "${WORKER_COUNTS[@]}"; do
  for i in {1..5}; do
    echo "Cloning fresh DB..."
    rm -rf /tank/rocksdbtest/updaterandom
    cp -r /tank/rocksdbtest/reopen /tank/rocksdbtest/updaterandom

    if [ "$RUNNER_MODE" = "threads" ]; then
      echo "Running benchmark with $W threads..."
      OUTPUT=$(./db_bench --db=/tank/rocksdbtest/updaterandom --benchmarks="updaterandom" --duration=30 --seed=425 --use_existing_db=1 --num=10000000 --ops_between_duration_checks=100 --histogram=1 --report_open_timing --use_existing_keys --open_files=1 --threads=$W)
      echo "$OUTPUT"
      OPS=$(echo "$OUTPUT" | grep "updaterandom :" | awk '{print $5}')
      MICROS_OP=$(echo "$OUTPUT" | grep "updaterandom :" | awk '{print $3}')
      P50=$(echo "$OUTPUT" | grep "Percentiles" | awk -F' ' '{print $3}')
      P95=$(echo "$OUTPUT" | grep "Percentiles" | awk -F' ' '{print $7}')
      P99=$(echo "$OUTPUT" | grep "Percentiles" | awk -F' ' '{print $11}')
      TIME=$(echo "$OUTPUT" | grep "updaterandom :" | awk '{print $7}')
      OPEN_TIME=$(echo "$OUTPUT" | grep "updaterandom :" | awk '{print $15}')
    else
      # Processes mode: run W db_bench processes in parallel. Each has 1 thread.
      echo "Running benchmark with $W processes..."
      TMPDIR=$(mktemp -d)
      for ((p = 0; p < W; p++)); do
        ./db_bench --db=/tank/rocksdbtest/updaterandom --benchmarks="updaterandom" --duration=30 --seed=$((425 + p)) --use_existing_db=1 --num=10000000 --reopen_after_each_op --ops_between_duration_checks=100 --histogram=1 --report_open_timing --use_existing_keys --open_files=1 --threads=1 >"$TMPDIR/out_$p" 2>&1 &
      done
      wait
      # Aggregate: sum the ops, take the ops-weighted mean of micros_op,
      # average the percentiles.
      TOTAL_OPS=0
      WEIGHTED_MICROS=0
      P50_SUM=0 P95_SUM=0 P99_SUM=0
      TIME=""
      VALID=0
      for ((p = 0; p < W; p++)); do
        OUT=$(cat "$TMPDIR/out_$p")
        echo "$OUT"
        OPS_TOTAL=$(echo "$OUT" | grep "updaterandom :" | awk '{print $9}')
        M=$(echo "$OUT" | grep "updaterandom :" | awk '{print $3}')
        P50_P=$(echo "$OUT" | grep "Percentiles" | awk -F' ' '{print $3}')
        P95_P=$(echo "$OUT" | grep "Percentiles" | awk -F' ' '{print $7}')
        P99_P=$(echo "$OUT" | grep "Percentiles" | awk -F' ' '{print $11}')
        T=$(echo "$OUT" | grep "updaterandom :" | awk '{print $7}')
        if [ -n "$OPS_TOTAL" ] && [ -n "$M" ]; then
          TOTAL_OPS=$((TOTAL_OPS + OPS_TOTAL))
          WEIGHTED_MICROS=$(echo "$WEIGHTED_MICROS $OPS_TOTAL $M" | awk '{printf "%.3f", $1 + $2 * $3}')
          P50_SUM=$(echo "$P50_SUM $P50_P" | awk '{printf "%.2f", $1 + $2}')
          P95_SUM=$(echo "$P95_SUM $P95_P" | awk '{printf "%.2f", $1 + $2}')
          P99_SUM=$(echo "$P99_SUM $P99_P" | awk '{printf "%.2f", $1 + $2}')
          TIME="$T"
          VALID=$((VALID + 1))
        fi
      done
      rm -rf "$TMPDIR"
      if [ "$VALID" -gt 0 ]; then
        OPS=$(echo "$TOTAL_OPS $TIME" | awk '{printf "%.0f", $1/$2}')
        MICROS_OP=$(echo "$WEIGHTED_MICROS $TOTAL_OPS" | awk '{printf "%.3f", $1/$2}')
        P50=$(echo "$P50_SUM $VALID" | awk '{printf "%.2f", $1/$2}')
        P95=$(echo "$P95_SUM $VALID" | awk '{printf "%.2f", $1/$2}')
        P99=$(echo "$P99_SUM $VALID" | awk '{printf "%.2f", $1/$2}')
      else
        OPS=0 MICROS_OP="" P50="" P95="" P99=""
      fi
      OPEN_TIME=""
    fi

    # Append the row to the CSV.
    echo "$W,$OPS,$MICROS_OP,$P50,$P95,$P99,$TIME,$OPEN_TIME" >>"$CSV_FILE"
  done
done

echo "Done! Results saved to $CSV_FILE"
