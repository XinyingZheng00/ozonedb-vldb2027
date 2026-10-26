#!/bin/bash
# Measure RocksDB db_bench with and without a reopen after each operation.
# The script creates one database. Then it runs two passes over the thread counts. The
# first pass runs updaterandom with --reopen_after_each_op (the "multi-process" rows).
# The second pass runs updaterandom without it (the "multi-thread" rows). For each cell
# the script copies a fresh database, runs for 30 s, and appends the throughput, the
# latency percentiles and the open time to a CSV file. It repeats each cell five times.
# Run the script from the RocksDB build directory that holds db_bench.

# Build the CSV file name from the current date and time.
CSV_FILE="results_$(date '+%Y%m%d_%H%M%S').csv"

# Write the CSV header.
echo "mode,threads,avg_ops_sec,p50_latency,p95_latency,p99_latency,time,open_time_ms" >"$CSV_FILE"

# The thread counts to test.
THREAD_COUNTS=(1 2 4 8 12 16 20 24)

echo "Creating DB at /tmp/rocksdbtest/reopen"
mkdir -p /tmp/rocksdbtest
./db_bench --db=/tmp/rocksdbtest/reopen --benchmarks="filluniquerandom" --num=10000000 --seed=425

for T in "${THREAD_COUNTS[@]}"; do
  for i in {1..5}; do
    echo "Cloning fresh DB..."
    rm -rf /tmp/rocksdbtest/updaterandom
    cp -r /tmp/rocksdbtest/reopen /tmp/rocksdbtest/updaterandom

    # Run db_bench and capture its output.
    # 'grep' finds the benchmark line. 'awk' pulls the columns.
    echo "Running benchmark with $T threads..."
    OUTPUT=$(./db_bench --db=/tmp/rocksdbtest/updaterandom --benchmarks="updaterandom" --duration=30 --seed=425 --use_existing_db=1 --num=10000000 --reopen_after_each_op --ops_between_duration_checks=100 --histogram=1 --report_open_timing --use_existing_keys --threads=$T)

    # Extract the values. The column positions can shift with your RocksDB version.
    OPS=$(echo "$OUTPUT" | grep "updaterandom :" | awk '{print $5}')
    P50=$(echo "$OUTPUT" | grep "Percentiles" | awk -F' ' '{print $3}')
    P95=$(echo "$OUTPUT" | grep "Percentiles" | awk -F' ' '{print $7}')
    P99=$(echo "$OUTPUT" | grep "Percentiles" | awk -F' ' '{print $11}')
    TIME=$(echo "$OUTPUT" | grep "updaterandom :" | awk '{print $7}')
    OPEN_TIME=$(echo "$OUTPUT" | grep "updaterandom :" | awk '{print $15}')

    # Append the row to the CSV.
    echo "multi-process,$T,$OPS,$P50,$P95,$P99,$TIME,$OPEN_TIME" >>"$CSV_FILE"
  done
done

for T in "${THREAD_COUNTS[@]}"; do
  for i in {1..5}; do
    echo "Cloning fresh DB..."
    rm -rf /tmp/rocksdbtest/updaterandom
    cp -r /tmp/rocksdbtest/reopen /tmp/rocksdbtest/updaterandom

    # Run db_bench and capture its output.
    # 'grep' finds the benchmark line. 'awk' pulls the columns.
    echo "Running benchmark with $T threads..."
    OUTPUT=$(./db_bench --db=/tmp/rocksdbtest/updaterandom --benchmarks="updaterandom" --duration=30 --seed=425 --use_existing_db=1 --num=10000000 --ops_between_duration_checks=1000 --histogram=1 --report_open_timing --use_existing_keys --threads=$T)

    # Extract the values. The column positions can shift with your RocksDB version.
    OPS=$(echo "$OUTPUT" | grep "updaterandom :" | awk '{print $5}')
    P50=$(echo "$OUTPUT" | grep "Percentiles" | awk -F' ' '{print $3}')
    P95=$(echo "$OUTPUT" | grep "Percentiles" | awk -F' ' '{print $7}')
    P99=$(echo "$OUTPUT" | grep "Percentiles" | awk -F' ' '{print $11}')
    TIME=$(echo "$OUTPUT" | grep "updaterandom :" | awk '{print $7}')
    OPEN_TIME=$(echo "$OUTPUT" | grep "OpenDb:" | awk '{print $2}')

    # Append the row to the CSV.
    echo "multi-thread,$T,$OPS,$P50,$P95,$P99,$TIME,$OPEN_TIME" >>"$CSV_FILE"
  done
done

echo "Done! Results saved to $CSV_FILE"
