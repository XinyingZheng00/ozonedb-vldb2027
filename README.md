# OzoneKV

OzoneKV is a research LSM-tree key-value store. All of its durable state lives behind an
append-only "shared log" storage abstraction. Multiple independent writer processes, on one
machine or on many, coordinate through the log itself. There is no server. The engine builds as a
shared library (`libOzoneDB.so`) with a C++ API and a JNI binding for a YCSB benchmark client.

The code, library and result labels say `ozonedb`. The paper says OzoneKV. They are the same
system.

## Citation

This repository is the artifact of the VLDB 2027 paper. See `CITATION.cff`. A reproduction guide
for every figure lives in the artifact repository, which pins this one at the tag `vldb2027`.

## Backends

| Backend | Config value | Use |
| --- | --- | --- |
| local filesystem | `local` | tests, single-machine runs |
| S3-compatible object store (MinIO, S3, R2, Wasabi) | `s3` | SSTables (`sstable_backend`) |
| CorfuDB shared log | `corfu` | the log layer for multi-writer runs (`backend`) |

The paper's configuration is `backend = corfu` for the three logs and `sstable_backend = s3` for
SSTables. An optional local SSD tier (`disk_cache_dir`) sits in front of the object store.

## 1. Set up a machine

```bash
bash bench/scripts/setup.sh --role client         # the bench / YCSB machine
bash bench/scripts/setup.sh --role corfu-server   # the shared-log node
bash bench/scripts/setup.sh --role minio          # the SSTable object store
```

The client role installs the apt dependencies and a JDK, clones vcpkg at the commit pinned in
`vcpkg.json`, creates `/tank`, and runs `bench/scripts/build.sh`. The first build compiles the
C++ dependencies from source. Expect about an hour for `aws-sdk-cpp`.

The script writes `~/.ozonedb.env` (with `OZONEDB_HOME`, `JAVA_HOME` and `PATH`) and wires a
marker-guarded include into `~/.profile` and `~/.bashrc`. Open a new shell afterwards, or run
`. ~/.ozonedb.env`.

## 2. Build

On NixOS, or with nix installed, `shell.nix` provides every dependency without vcpkg:

```bash
nix-shell --run 'cmake -B build -G Ninja -DOZONEDB_USE_VCPKG=OFF -DOZONEDB_ENABLE_CORFU=ON && cmake --build build'
```

Everywhere else:

```bash
cmake -B build                           && cmake --build build -j$(nproc)  # local FS + S3
cmake -B build -DOZONEDB_ENABLE_CORFU=ON && cmake --build build -j$(nproc)  # adds CorfuDB
```

CMake finds vcpkg in this order: an explicit `CMAKE_TOOLCHAIN_FILE`, then `$VCPKG_ROOT`, then
`vcpkg/` inside the repo (which `setup.sh` creates). With CMake 3.21 or newer the presets work
too: `cmake --preset default`, `--preset corfu`, or `--preset profiling`.

After any C++ change that a YCSB run must see, rebuild the whole chain:

```bash
bash bench/scripts/build.sh
```

## 3. Test

```bash
cd build && ctest --output-on-failure
./runUnitTests --gtest_filter='StorageTest.*'       # one suite
```

Run the tests from `build/`. They reference configs by relative path. They write under
`OZONEDB_TEST_ROOT` (default `/tank/test/`), so set it to any writable directory. `CorfuStorageTest.*` skip themselves unless `CORFU_TEST_ENDPOINT` names a fresh Corfu
server. `bench/scripts/corfu_local.sh` builds CorfuDB at the pinned commit and runs them against a
fresh single-node server on this machine, without apt or sudo. On NixOS, `corfu-server.nix`
provides its JDK and Maven:

```bash
nix-shell corfu-server.nix --run 'bash bench/scripts/corfu_local.sh build'   # once
nix-shell corfu-server.nix --run 'bash bench/scripts/corfu_local.sh test'    # CorfuStorageTest.*
```

`S3StorageTest.*` and `S3DBTest.*` likewise skip unless `S3_TEST_ENDPOINT` is set.
`bench/scripts/minio_local.sh` runs them against a fresh MinIO on `127.0.0.1:9000`, and on NixOS
`minio.nix` provides MinIO and `mc`:

```bash
nix-shell minio.nix --run 'bash bench/scripts/minio_local.sh test'           # S3StorageTest.*, S3DBTest.*
```

The Corfu smoke binaries exercise the full multi-writer path:

```bash
./corfu_smoke <shared-config.json> [num_keys]
./corfu_multiwriter_smoke <shared-config.json> [keys_per_writer]
./corfu_multiwriter_smoke <shared-config.json> 2000 --kill-one   # dead-writer takeover demo
```

`--kill-one` kills one of the two writers half-way through its range; the other stays open past
`task_heartbeat_threshold_ticks`, reclaims the dead writer's compaction claim and finishes it, and
the parent checks every acked key. It fails unless at least one takeover happened.

## 4. Use the C++ API

```cpp
#include "db.h"
using namespace ozonedb;

DB* db = nullptr;
DB::openDB(db, "shared_config.json");       // one config file per process
db->put("key", "value");

std::string const* value = nullptr;
std::shared_ptr<Record> guard;              // keeps *value alive
if (db->get("key", value, guard) == Status::kSuccess) { /* use *value */ }

db->remove("key");
DB::closeDB(db);
```

`value` aliases bytes inside `guard`. Keep `guard` alive while you read `value`. The JNI surface
(`ycsb/ozonedb/src/main/java/jni/OzoneDBJNI.java`) exposes the same four calls plus
`sync`/`clearSync` for batched linearizable reads.

### Config keys

One flat JSON file per process. Every value is a string. The base files in `src/config/` show
the full set. The ones you will touch:

| Key | Meaning |
| --- | --- |
| `backend` | `local` or `corfu` for the logs |
| `corfu_endpoint`, `corfu_stream_name` | the Corfu server and stream |
| `sstable_backend`, `s3_*`, `sstable_dir` | where SSTables live |
| `db_path` | local scratch directory, unique per process |
| `log_file_size_limit`, `level_size`, `level_file_size_limit`, `max_level` | LSM shape |
| `lru_cache_bytes` | block cache per process |
| `task_heartbeat_threshold_ticks` | 100 ms ticks without a heartbeat before a compaction claim is dead and taken over (default 30) |
| `compaction_enabled` | `false` opens a process that never compacts |
| `compaction_abort_rate` | fault injection: probability that a claimed compaction is abandoned (default 0) |
| `linearizable_reads` | one fence per get, strict reads across writers |
| `trust_background_tail` | serve log reads from the in-memory key index |
| `disk_cache_dir`, `disk_cache_bytes` | local SSD tier in front of the object store |
| `log_trim_enabled`, `log_trim_interval_ms` | checkpoint to the object store and trim the log |

`CLAUDE.md` is the architecture document: the three logs, the View, the task log, the ack rule
of the Corfu backend, checkpoints and trimming.

## 5. Run benchmarks

Copy `bench/scripts/config/ycsb.example.yaml` to `ycsb.yaml` and fill in the `nodes:` block.
Every runner refuses to start without it. The file is gitignored.

```bash
# single machine, N parallel writer processes
python3 bench/scripts/local/load_local_ycsb_multiproc.py   # load phase
python3 bench/scripts/local/run_local_ycsb_multiproc.py    # run phase

# sweep workloads x writer counts, restarting Corfu between iterations
bash bench/scripts/local/run_ycsb_with_corfu.sh --workloads "a c" --writers-list "1 2 4"

# fan out across cluster hosts
bash bench/scripts/local/run_multinode_ycsb_with_corfu.sh --linearizable --trials 3
```

The single-node comparison (Fig 2a, 9, 15, 19a and Table 4 of the paper) has its own drivers under
`bench/scripts/local/singlenode/` and `bench/scripts/local/churn_experiment/`; see `CLAUDE.md`.

Results land in `bench/results/local` (gitignored). Plot scripts are in `bench/scripts/plot/`.
`bench/ansible/` pushes the tree to every host and runs `setup.sh` there. See
`bench/ansible/README.md`.

## Known issues

`KNOWN-ISSUES.md` lists the correctness hazards that a code audit found and that are not fixed.
Read it before you run the engine outside the benchmark setup.

## License

Apache-2.0. See `LICENSE` and `NOTICE`. The SSTable layer derives from LevelDB (BSD-3-Clause,
`LICENSE-leveldb`), the YCSB tree from YCSB (Apache-2.0), and `src/corfu_proto/` from CorfuDB
(Apache-2.0).
