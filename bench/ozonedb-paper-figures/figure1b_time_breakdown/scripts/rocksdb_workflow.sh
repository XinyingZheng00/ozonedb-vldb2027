#!/bin/bash
# Build RocksDB db_bench and run the reopen benchmark.
# The script installs the build dependencies, builds db_bench, runs
# tools/reopen_benchmark.sh in processes mode and plots the CSV result. The commented
# commands at the end record the perf runs that analyse_time_share.py reads.
# Run the script from the root of a RocksDB checkout that has the two tools scripts.
sudo apt-get update && sudo apt-get install libgflags-dev python3-pip linux-tools-common linux-tools-generic && sudo apt-get install libsnappy-dev
make clean && make db_bench DEBUG_LEVEL=0 -j$(nproc)

# tools/reopen_benchmark.sh reads RUNNER_MODE from the environment, not from argv.
# Capture the CSV path from its final "Done! Results saved to ..." line.
# echo "Running threads benchmark"
# RUNNER_MODE=threads ./tools/reopen_benchmark.sh 2>&1 | tee reopen_threads.log || exit 1
# CSV_FILE_THREADS=$(grep "Done! Results saved to" reopen_threads.log | tail -1 | awk '{print $5}')
# echo "CSV file: $CSV_FILE_THREADS"
# echo "Threads benchmark completed"

echo "Running processes benchmark"
RUNNER_MODE=processes ./tools/reopen_benchmark.sh 2>&1 | tee reopen_processes.log || exit 1
CSV_FILE_PROCESSES=$(grep "Done! Results saved to" reopen_processes.log | tail -1 | awk '{print $5}')
echo "CSV file: $CSV_FILE_PROCESSES"
echo "Processes benchmark completed"

echo "Plotting results"
pip install matplotlib
python3 tools/plot_reopen_results.py $CSV_FILE_THREADS $CSV_FILE_PROCESSES -o result.pdf
echo "Plotting completed"

# Get the breakdown plot of the benchmark.
# make clean
# make db_bench DEBUG_LEVEL=1 -j$(nproc)

# rm -rf /tank/rocksdbtest/updaterandom
# cp -r /tank/rocksdbtest/reopen /tank/rocksdbtest/updaterandom
# sudo perf record -F 99 -g -o perf_updaterandom_reopen.data -- ./db_bench --db=/tank/rocksdbtest/updaterandom --benchmarks="updaterandom" --duration=100 --seed=425 --use_existing_db=1 --num=10000000 --ops_between_duration_checks=100 --histogram=1 --report_open_timing --use_existing_keys  --open_files=1 --threads=4 --reopen_after_each_op
# sudo perf record -F 99 -g -o perf_updaterandom_no_reopen.data -- ./db_bench --db=/tank/rocksdbtest/updaterandom --benchmarks="updaterandom" --duration=100 --seed=425 --use_existing_db=1 --num=10000000 --ops_between_duration_checks=100 --histogram=1 --report_open_timing --use_existing_keys  --open_files=1 --threads=4
