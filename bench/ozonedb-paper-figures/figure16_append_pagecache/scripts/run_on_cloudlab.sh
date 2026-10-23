#!/usr/bin/env bash
# Run the full Figure 16 sweep on a CloudLab c6525-25g node.
#
#   bash run_on_cloudlab.sh <dir on an ext4 SATA SSD>
#
# 2 modes x 2 sync policies x N in {1,2,4,8,16,32} x 3 repeats, 10 s each:
# 72 runs, about 15 minutes. Results go to ../data/cloudlab_<host>_<time>/.
set -euo pipefail

DIR="${1:?usage: $0 <dir on an ext4 SATA SSD, e.g. /mnt/ssd/appendtest>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$DIR"

FS="$(findmnt -n -o FSTYPE -T "$DIR")"
DEV="$(findmnt -n -o SOURCE -T "$DIR")"
echo "target: $DIR  filesystem: $FS  device: $DEV"
lsblk -d -o NAME,ROTA,SIZE,MODEL 2>/dev/null || true
if [ "$FS" != "ext4" ]; then
  echo "error: $DIR is on $FS, not ext4 (the paper used ext4)." >&2
  echo "Format a spare SSD first, e.g.:" >&2
  echo "  sudo mkfs.ext4 -F /dev/sdb && sudo mkdir -p /mnt/ssd && sudo mount /dev/sdb /mnt/ssd && sudo chown \$USER /mnt/ssd" >&2
  exit 1
fi

command -v c++ >/dev/null || sudo apt-get install -y build-essential
OUT="$HERE/../data/cloudlab_$(hostname -s)_$(date +%Y%m%d_%H%M)"
python3 "$HERE/run_sweep.py" --dir "$DIR" --out "$OUT" \
  --procs 1,2,4,8,16,32 --duration 10 --repeats 3 --drop-caches
echo
echo "done: $OUT/summary.csv"
echo "copy it back with:  scp <node>:$OUT/summary.csv <local figure16_append_pagecache/data/>"
