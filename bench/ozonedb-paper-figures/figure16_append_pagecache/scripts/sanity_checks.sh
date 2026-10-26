#!/usr/bin/env bash
# Sanity checks for Figure 16: measure what should limit each curve.
#
#   bash sanity_checks.sh <dir on the ext4 SSD used for the sweep>
#
# The script takes about 4 minutes. It writes everything to ../data/sanity_<host>_<time>/:
#   system.txt      kernel, disk model, write-cache mode, mount options, dirty-page limits
#   latency.csv     single-writer per-operation latencies (latency_probe.cpp)
#   jbd2_<run>.txt  ext4 journal stats before and after fsync runs (group commit)
#   io_<run>.csv    0.5 s samples of Dirty/Writeback pages and device bytes written
#   runs.csv        the append_bench runs the stats belong to
set -euo pipefail

DIR="${1:?usage: $0 <dir on the ext4 SSD>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/../data/sanity_$(hostname -s)_$(date +%Y%m%d_%H%M)"
mkdir -p "$DIR" "$OUT"

DEV="$(findmnt -n -o SOURCE -T "$DIR")"   # e.g. /dev/sdb
BLK="$(basename "$DEV")"                  # e.g. sdb
DISK="$(lsblk -no PKNAME "$DEV" 2>/dev/null || true)"; DISK="${DISK:-$BLK}"
JBD2="$(ls -d /proc/fs/jbd2/${BLK}-* 2>/dev/null | head -1)/info"

c++ -O2 -std=c++17 -o "$HERE/latency_probe.linux" "$HERE/latency_probe.cpp"
c++ -O2 -std=c++17 -o "$HERE/append_bench.linux" "$HERE/append_bench.cpp"

{
  echo "== kernel";            uname -a
  echo "== mount";             findmnt -T "$DIR"
  echo "== disk";              lsblk -d -o NAME,ROTA,SIZE,MODEL "/dev/$DISK"
  echo "== write cache";       cat "/sys/block/$DISK/queue/write_cache"
  echo "== fua";               cat "/sys/block/$DISK/queue/fua" 2>/dev/null || true
  echo "== hdparm -W";         sudo hdparm -W "/dev/$DISK" 2>/dev/null || true
  echo "== dirty limits";      sysctl vm.dirty_ratio vm.dirty_background_ratio vm.dirty_bytes vm.dirty_background_bytes vm.dirty_expire_centisecs
  echo "== memory";            grep -E 'MemTotal|Dirty|Writeback:' /proc/meminfo
} > "$OUT/system.txt" 2>&1
cat "$OUT/system.txt"

drop() { sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'; }

echo; echo "== per-operation latencies (single writer)"
drop
"$HERE/latency_probe.linux" --path "$DIR/probe.dat" --ops 5000 | tee "$OUT/latency.csv"

# Sample Dirty/Writeback (kB) and device sectors written every 0.5 s while a command runs.
sample() {  # sample <out.csv> <cmd...>
  local out="$1"; shift
  echo "t_s,dirty_kb,writeback_kb,dev_written_mb" > "$out"
  ( i=0; while :; do   # i counts half-seconds
      d=$(awk '/^Dirty:/{print $2}' /proc/meminfo); w=$(awk '/^Writeback:/{print $2}' /proc/meminfo)
      s=$(awk '{print $7}' "/sys/block/$DISK/stat")
      echo "$((i / 2)).$((i % 2 * 5)),$d,$w,$((s * 512 / 1048576))" >> "$out"; sleep 0.5; i=$((i + 1))
    done ) & local pid=$!
  "$@"; local rc=$?
  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null || true
  return $rc
}

run() {  # run <name> <mode> <sync> <procs> <seconds>
  local name="$1"; shift
  drop
  [ -r "$JBD2" ] && cat "$JBD2" > "$OUT/jbd2_${name}_before.txt"
  sample "$OUT/io_${name}.csv" "$HERE/append_bench.linux" --path "$DIR/append.dat" \
      --mode "$1" --sync "$2" --procs "$3" --duration "$4" --verify | tee -a "$OUT/runs.csv"
  [ -r "$JBD2" ] && cat "$JBD2" > "$OUT/jbd2_${name}_after.txt"
}

echo; echo "== append_bench runs with journal and I/O sampling"
echo "mode,sync,procs,record_bytes,seconds,total_ops,ops_per_sec,verified" > "$OUT/runs.csv"
run fsync_buffered_n1  buffered fsync 1  10
run fsync_buffered_n32 buffered fsync 32 10
run fsync_direct_n32   direct   fsync 32 10
run nosync_direct_n1   direct   none  1  10
run nosync_direct_n32  direct   none  32 10
run nosync_buffered_n1 buffered none  1  10
run nosync_buffered_n1_long buffered none 1 120   # long enough to reach dirty-page throttling
rm -f "$DIR/append.dat"

echo; echo "done: $OUT"
echo "copy back:  scp -r '<node>:$OUT' <local figure16_append_pagecache/data/>"
