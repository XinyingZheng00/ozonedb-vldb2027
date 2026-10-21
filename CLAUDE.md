# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OzoneKV (the code, library and result labels still say `ozonedb` / `libOzoneDB.so`) is a research
LSM-tree key-value store whose entire durable state lives behind an append-only "shared log"
storage abstraction. Multiple independent writer *processes* (possibly on different machines)
coordinate through the log itself — there is no server. It is built as a shared library, driven
through a JNI binding from a YCSB benchmark client.

## Environment

`OZONEDB_HOME` must be exported and point at the repo root — every bench script reads it, and
`jni.OzoneDBJNI` uses it to locate `libozonedb.so`. CMake does not need it. The build targets
Ubuntu bench nodes (apt packages, a writable `/tank`) — it is not set up to build on macOS.

Setup is one idempotent, role-based script. Re-running never duplicates shell config or
reinstalls what is present:

```bash
bash bench/scripts/setup.sh --role client         # apt deps, JDK, python, /tank,
                                                  # vcpkg clone + bootstrap, then build.sh
bash bench/scripts/setup.sh --role corfu-server   # CorfuDB + /mnt/corfu/{load,run_batch}
bash bench/scripts/setup.sh --role minio          # minio + bucket (sstable_backend defaults to s3)
bash bench/scripts/setup_zfs.sh --device /dev/sdXN
bash bench/scripts/setup_disk_cache.sh --device /dev/sdb   # ext4 label ozcache at /tank/cache (disk-cache tier)
```

- It writes `~/.ozonedb.env` and wires a marker-guarded include into `~/.profile` **and**
  `~/.bashrc`, rewriting the block rather than appending. Sourcing the script is not needed.
  Note `~/.bashrc` alone is not enough: Ubuntu's default returns early for non-interactive shells,
  which is why `run_multinode_ycsb.py` uses `bash -lc`.
- **vcpkg is not vendored.** `vcpkg.json` pins a `builtin-baseline`; the client role clones vcpkg
  at that commit into `$OZONEDB_HOME/vcpkg` (gitignored) and bootstraps it. CMake finds it there,
  or through `$VCPKG_ROOT`, or through an explicit `CMAKE_TOOLCHAIN_FILE`, and fails loudly when
  none exists. The first build compiles the dependencies (`aws-sdk-cpp` is the slow one).
- **JDK policy.** The client needs a JDK only for YCSB (`<source>1.8</source>` in `ycsb/pom.xml`)
  and the JNI shim. The engine talks to CorfuDB with its own C++ client. `--jdk` (default 25) sets
  the node's persistent `JAVA_HOME`; `--corfu-jdk` (default 25) is used only by the corfu-server
  role to build CorfuDB.
- `setup_node.sh` and `setup_corfu.sh` are thin wrappers kept for compatibility.
- Python dependencies are declared in `bench/scripts/requirements.txt`.
- `bench/scripts/config/ycsb.yaml` is gitignored and per cluster. Copy `ycsb.example.yaml` and
  fill in the `nodes:` block; every runner refuses to start without it.

## Build

```bash
cmake -B build                           && cmake --build build -j$(nproc)  # local FS + S3
cmake -B build -DOZONEDB_ENABLE_CORFU=ON && cmake --build build -j$(nproc)  # adds CorfuDB
```

`CMakePresets.json` defines `default` / `corfu` / `profiling` (toolchain from `$VCPKG_ROOT`), but
presets need CMake ≥ 3.21 and the Ubuntu 20.04 bench image ships 3.16 — hence the plain commands
above as the primary path. `CMakeUserPresets.json` is per-user and gitignored.

- `OZONEDB_ENABLE_S3` defaults **ON** (needs `aws-sdk-cpp` from vcpkg); `OZONEDB_ENABLE_CORFU`
  defaults **OFF**. Both set a `-D` compile definition that guards the corresponding
  `src/db/*_storage.cpp` and its header — new backend code must be inside those guards.
- `-DOZONEDB_PROFILING=ON` keeps `-O3` but adds frame pointers + line tables for perf/flamegraph.
- `OZONEDB_BUILD_JNI` (default ON) builds `libozonedb.so`, the shim the YCSB binding loads. It is
  the only target that needs a JDK; without `jni.h` it is skipped with a message.
- Protobuf headers are generated into `build/generated/protobuf/` at configure time from
  `record.proto` / `sstable.proto`. Sources include them as `"protobuf/record.pb.h"`, and
  `build/generated` is deliberately **first** on the include path so a stale in-tree copy from a
  pre-move build can't shadow them (configure also deletes that directory if it finds it).
- Header/link deps come from `target_include_directories(OzoneDB PUBLIC …)`, so anything linking
  `OzoneDB` inherits them. Don't reintroduce global `include_directories()` or `link_directories()`.
- `vcpkg.json` carries only what is actually linked: `boost-uuid`, `gtest`, `openssl`, `protobuf`,
  `aws-sdk-cpp[s3]`.

**After any C++ change that a YCSB run must see, rebuild the whole chain:**

```bash
bash bench/scripts/build.sh
```

That builds every CMake target (`OzoneDB`, `ozonedb_jni`, `runUnitTests`, the Corfu smoke
binaries), then the YCSB `ozonedb-binding`. Nothing is installed outside the repo — no `sudo`, no
`/usr/lib` — because `libozonedb.so` has an `$ORIGIN` rpath to reach `libOzoneDB.so` beside it in
`build/`. Skipping it means YCSB keeps running the previously built library.

## Tests

```bash
cd build && ctest --output-on-failure                     # all (one gtest binary)
./runUnitTests --gtest_filter=DBTest.single_put_get      # one test
./runUnitTests --gtest_filter='StorageTest.*'            # one suite
```

- **Run from `build/`** — tests reference configs by relative path (`../src/config/...`).
- `/tank` must exist and be writable: `sudo mkdir -p /tank && sudo chmod 777 /tank`.
- `CorfuStorageTest.*` self-skip unless `CORFU_TEST_ENDPOINT` is set, and need a **fresh** server
  (they fail-stop on a trimmed log).
- Corfu end-to-end smoke binaries (only built with `OZONEDB_ENABLE_CORFU=ON`) are the practical way
  to exercise the shared-log path:
  ```bash
  ./corfu_smoke <shared-config.json> [num_keys]
  ./corfu_multiwriter_smoke <shared-config.json> [keys_per_writer]
  ```
- `KNOWN-ISSUES.md` lists the correctness hazards found by the 2026-09 audit that are not fixed.

## Benchmarks

Everything is driven by `bench/scripts/config/ycsb.yaml`, loaded through
`bench/scripts/ycsb_config.py` — never `yaml.safe_load` directly. The `nodes:` block names each
machine once with **two non-interchangeable addresses**: `ssh` (public, how the orchestrator
reaches it) and `lan` (internal 10.10.1.x, how other nodes reach it). `corfu_server -a` binds the
*lan* one, so clients can only connect if that is what `corfu.endpoint` resolves to. The loader
derives `corfu.endpoint`, `s3.endpoint` and `cloudlab.{hosts,ssh_user,ssh_private_key_path}` from
`nodes:` and materialises them under the keys consumers already read, and **rejects** any of those
set by hand. `python3 bench/scripts/ycsb_config.py --check` prints the resolved plan; the shell
wrappers read the corfu node through its `--node`/`--get` CLI rather than a hardcoded constant.

```bash
python3 bench/scripts/local/load_local_ycsb_multiproc.py   # load phase, N parallel writer procs
python3 bench/scripts/local/run_local_ycsb_multiproc.py    # run phase
bash    bench/scripts/local/run_ycsb_with_corfu.sh --workloads "a c" --writers-list "1 2 4"
python3 bench/scripts/local/run_multinode_ycsb.py          # fan out across CloudLab hosts via scp/ssh
python3 bench/scripts/local/run_corfu_compaction_contention.py
```

- `run_ycsb_with_corfu.sh` restarts the remote Corfu server between every (trial, workload, writers)
  iteration and tags all results with one `OZONEDB_RUN_TAG`.
- `run_multinode_ycsb_with_corfu.sh` is the cluster path (`run_multinode_ycsb.py` →
  `run_local_ycsb_multiproc.py` on every client). Campaign-driver flags: `--linearizable`,
  `--duration S`, `--trial N` (one trial; `--trials N` runs 1..N), `--run-tag TAG`, `--dry-run`.
  `--linearizable` forces `linearizable_reads=true` + `trust_background_tail=false` into every
  generated per-writer config (`linearizable_corfu_settings`, shared with `consistency.py`) and
  suffixes the result-file engine token — `ozonedb-corfu-linearizable_w0of8_…` — while `db_name`
  itself never changes (the runner branches on it). Select the read mode with the flag, not by
  editing `ycsb.yaml`: the label is derived from the effective settings either way, but a YAML
  toggle has to be synced to every client. `local.run.repeated` must be 1 — the run phase refuses
  anything else because repeats overwrite the `_trial{N}` files; use trials.
- Every `ozonedb-corfu` result file carries a `-native` token (`ozonedb-corfu-native_…`). It dates
  from the A/B against the retired embedded-JVM Corfu client; the plot and extract scripts key on
  it, so it stays.
- **Cassandra baseline** (server-based comparison for the consistency/throughput frontier).
  `ycsb/cassandra/` is upstream YCSB's CQL binding plus a `cassandra.lwt` switch (every write an
  `IF [NOT] EXISTS` Paxos round) — see its README. `setup.sh --role cassandra` installs the Apache
  tarball on every `nodes.cassandra` entry (default: the log node, so both systems get the same
  server box; **never run Corfu and Cassandra benchmarks at once**) with its own JDK 17 under
  `cassandra.install_dir/env.sh`. `bench/scripts/cassandra_ctl.sh` runs on the server
  (`start|stop|wipe|schema|save-load|restore-load`); `save-load`/`restore-load` are the
  `/mnt/corfu/{load,run_batch}` pattern so every trial starts from the drained load snapshot.
  `load_multinode_cassandra.sh` wipes, creates the schema, loads from one client and snapshots;
  `run_multinode_ycsb_with_cassandra.sh --consistency one|quorum|serial` is the sweep, passing
  `--db_name cassandra --cassandra_consistency MODE` to every client (a flag, never a `ycsb.yaml`
  edit) and labelling results `cassandra-<mode>`. `serial` = SERIAL reads + LWT writes, the
  linearizable point; `one` == `quorum` at RF=1.
- Each writer process gets its **own generated config**: `_make_local_config_per_writer` /
  `_make_corfu_config_per_writer` template `src/config/{local,corfu}/shared_config_base.json` into
  `shared_config_w{i}.json`. Never point multiple writers at a single config file.
- Results go to `bench/results/local` (gitignored); plotting scripts live in `bench/scripts/plot/`.
  The tracked `bench/results-*.tsv` files are the cost-model inputs `plot_cost_model.py` reads.
- `bench/ansible/` fans out to every `cloudlab.hosts` entry in parallel. `bootstrap.yml` pushes the
  tree (without `vcpkg/` and `build/`) and then runs `setup.sh`, which clones vcpkg and builds on
  the node — no GitHub credentials on the node. `sync.yml` is the incremental follow-up and
  excludes `.git/`. Exclude lists are shared in `bench/ansible/group_vars/all.yml`; `inventory.py`
  is a dynamic inventory reading `cloudlab.{hosts,ssh_user,ssh_private_key_path}` straight out of
  `ycsb.yaml`, so the host list is never duplicated. The result pull stays on `scp` inside
  `run_multinode_ycsb.py`.

## Architecture

### Storage abstraction

`Storage` (`src/include/ozonedb/storage.h`) is the seam: named "files" supporting
`append`/`appendInBatch`/`read`/`size`/`seal`/`remove`. Implementations: `FileStorage` (local FS),
`S3Storage` (also MinIO/R2/Wasabi), `CorfuDBStorage` (shared log), and the `DiskCacheStorage`
decorator.

A DB holds **two** `Storage*`: `log_storage` (needs atomic append — the shared log) and
`sstable_storage` (SSTables are immutable and fit a plain object store better; paper §3.5). Set via
the `sstable_backend` config key. When unset the two pointers **alias**, and `DB::~DB` guards the
double-free. Anything touching either pointer must respect that aliasing.

### The three logs (all on `log_storage`)

| Log | Handler | Contents |
|---|---|---|
| data log (`datalog*`) | `LogHandler` | `Record` {key, value, type} — the memtable equivalent; sealed at `log_file_size_limit` |
| metadata log (`metadata.log`) | `MetadataLogHandler` | `OperationRecord` {LOGCREATE, COMPACT} |
| task log (`task.log`) | `TaskLogHandler` | `TaskRecord` — distributed compaction claims |

Every process rolls the metadata log forward into a **`View`**: storage layout per level, per-file
key ranges, file sizes, current log tail. The View is the shared source of truth about which files
exist where; it is published as an immutable `shared_ptr<View const>` snapshot and read lock-free
(readers must not deep-copy it — that was a hot-path regression that has already been removed once).

The task log is how concurrent writers avoid duplicating compaction: a writer claims a task by
appending a `TaskRecord` (owner fingerprint + `owner_generation`), heartbeats it, and dead owners'
tasks get reclaimed.

### Read / write paths

`DB::put` appends a `Record` to the data log. A background `CompactionWatcher` thread picks up
compaction (the only live `compaction_policy`, value 0; the config parser rejects anything else).

`DB::get`:
1. optional `LogKeyIndex` fast path (only when `trust_background_tail` is set) — skips the View load
   and the storage fence entirely;
2. atomic View snapshot, handed down to the child handlers as a raw pointer that aliases into it;
3. `LogHandler::readRecord` across log files;
4. `SSTableHandler::readRecordFromAllLevel`.

`linearizable_reads` (mutually exclusive with `trust_background_tail` — `Metadata` throws on both)
makes every get strict, at the cost of exactly **one fence per get**: `DB::get` opens a
`Storage::SyncScope` (one sequencer round-trip + tailer wait — the linearization point), which
records a thread-local fence token that every later storage call in the get reuses instead of
re-fencing; `MetadataLogHandler::syncView()` then rolls the view forward from local state, the
unfenced key-index probe in `readRecord` is bypassed, the log scan runs inline on the caller thread
(the token is thread-local, and post-fence reads are local-memory splices), and a post-scan size
check on `metadata.log` retries the get if a LOGCREATE/COMPACT was applied mid-scan (otherwise a
peer compaction's REMOVE can make a key transiently invisible; retries reuse the original fence).
`DB::sync()`/`clearSync()` (JNI: `OzoneDBJNI.sync`/`clearSync`) expose the fence as a batch API:
one fence on the calling thread, reused by every get until cleared — each read then linearizes at
the sync() point (`DB::get` skips its own fence when the thread already holds a token). The
end-to-end guarantee assumes the sync write defaults (`commit_interval_ = 0`,
`sync_mode_ = true`).

The returned `value` **aliases bytes inside the `guard` `shared_ptr<Record>`** — callers must keep
the guard alive while dereferencing.

### Caching

`LRUCache` (`cache.h`) holds parsed log records *and* SSTable blocks/tables, bounded by
`lru_cache_bytes`. Records are `shared_ptr`-owned so eviction/compaction can't free bytes under a
concurrent reader; cold block and table loads are single-flighted.

`DiskCacheStorage` (`src/db/disk_cache_storage.cpp`) is a second tier *below* the LRU: a `Storage`
decorator that `DB::DB` wraps around `sstable_storage`, keeping SSTable data as local files under
`disk_cache_dir` (a client's own SSD, `/tank/cache` on the bench nodes). Only names with the
`sstable` prefix are cacheable; everything else passes through. Config keys (`Metadata`):
`disk_cache_dir` (empty = off, and it throws without `sstable_backend`), `disk_cache_bytes`
(required with the dir), `disk_cache_drop_pages` (default true — `posix_fadvise(DONTNEED)` after a
tier read, so the measurement is the SSD and not the page cache).

Two modes. `disk_cache_mode = chunk` (**default**): the entry is a `disk_cache_entry_bytes` piece
of an SSTable (65536, a power of two ≥ 4096) — one sparse local file per SSTable plus one state
byte per chunk, a miss fetches the chunk-aligned covering range in **one** GET on the caller's
thread (the fill *is* the demand read), and eviction is per chunk through a CLOCK hand and
`fallocate(PUNCH_HOLE|KEEP_SIZE)`. `disk_cache_mode = file` keeps whole SSTables with a background
fill worker (`disk_cache_chunk_bytes` ranged reads, `disk_cache_fill_queue`) and file-level LRU; it
lost the measured A/B at every budget below the dataset size and is kept only for that comparison.
`disk_cache_admission` (**default `frequency`**): TinyLFU (`frequency_sketch.h`, count-min, four
rows of 8-bit counters halved every `disk_cache_admit_window` samples — 0 = 8× the entries the
budget holds) — budget that is *not free* is taken only when the candidate's estimated frequency
beats the victim's; free budget is never contested, and a write-through has no read history so it
takes free budget only. `always` is evict-to-fit. Reconcile in `chunk` mode starts cold, and a
whole-file read is served locally only when every chunk is present. `DB::closeDB` prints a
`[disk_cache] hits=… misses=… fills=… fill_gets=… evictions=… invalidated=… files=… capacity=…`
line after `[lru_cache]`. An unusable `disk_cache_dir` is fatal, not degraded: the constructor
throws `std::runtime_error` when the directory cannot be created or fails a write probe, and
`DB::DB` propagates it. The bench path is `--disk-cache-bytes` / `--disk-cache-dir` /
`--disk-cache-keep-pages` / `--disk-cache-mode` / `--disk-cache-entry-bytes` /
`--disk-cache-admission` on all three runner layers, per writer, label token
`-dc<size>[-ch<entry>][-adm][-kp]` — a flag, never a `ycsb.yaml` edit. The runner refuses a tier
root that is on the root filesystem *or* is not itself a mount point (`OZONEDB_DISK_CACHE_ANY_FS=1`
overrides), because `/tank` is its own filesystem and an unmounted SSD would otherwise land the
tier there.

### CorfuDB backend

All files are packed into one Corfu stream; each entry is a `CorfuEntry` protobuf
{`file_name`, `op` (APPEND/SEAL/REMOVE/TRIM), `payload`, `client_id`}. A background tailer
reconstructs per-file buffers and is the **only** writer of those buffers, own entries included,
so every process holds each file in address order, which is what makes the task-log election
("first claim in `task.log`") the same in every process. An append is acked only once its
outcome is **proven**, never assumed: the seal rule (an APPEND sequenced above its file's SEAL
belongs to no file) is decided either by the sequencer at token time or by the tailer.
`submitBatchFast` sends the file name as a Corfu conflict key in the token request's read set
at snapshot `last_applied_addr_`; every `SEAL`/`REMOVE` carries the same key in its write set,
so a granted token proves no seal sits between the snapshot and the new address and the put
returns without waiting for the tailer. A refused token has no address. When the sequencer
cannot vouch for the snapshot (right after a sequencer restart or a trim) the slow path takes a
plain token and waits for the tailer to pass it (`waitForTailerLocked`). Acking on the writer's
partial knowledge lost ~0.7 % of an 8-writer load and let several writers execute the same
compaction. **Never append a `SEAL` or `REMOVE` without the key**, and never mix builds that
predate the key in one cell: a keyless seal is invisible to every peer's fast path.
`corfu_fast_ack = false` selects the slow path for every append (A/B only). `client_id` only
decides whether the remote-append listener fires (own records are indexed by the writer path).
Reads fence on the writer's last-known address for read-my-writes. **The tailer never invokes
the remote-append listener synchronously** — a dedicated dispatch thread does, so the per-entry
parse and cache put stay off the tailer's critical path, which every fenced reader waits on.
Preserve that separation when touching `corfu_storage.cpp`.

**The Corfu client.** `CorfuDBStorage` drives the stream through the `CorfuClient` seam
(`src/include/ozonedb/corfu_client.h`), implemented once by the C++ client in `src/db/corfu/`
that speaks the Corfu protobuf protocol over TCP (protos vendored under `src/corfu_proto/`, kept
byte-identical to upstream so they can be diffed). No JVM is involved. It writes codec-NONE
entries with the stream in `BACKPOINTER_MAP` and raw file-name conflict keys, the same bytes the
Java runtime writes, so a Java writer on the same stream interoperates; a dataset holding ZSTD
entries from an older Java writer is refused. The reader scans the **global** address space in
order and assumes every entry belongs to the one OzoneDB stream (`corfu_native_probe` checks
that). Tuning keys: `corfu_read_batch`, `corfu_idle_poll_ms`, `corfu_hole_fill_timeout_ms`,
`corfu_request_timeout_ms`.

**Checkpoints and trimming.** A process with `log_trim_enabled = true` runs a `LogTrimmer`
(`src/db/log_trimmer.cpp`): every `log_trim_interval_ms` it takes an exact snapshot of the live
log state at one address `C` (`CorfuDBStorage::takeSnapshot`, which holds `write_gate_`
exclusively — every local submit path holds it shared, never twice on one thread), writes it to
`sstable_storage` under `<sstable_dir>/checkpoint/<C>/` with `LATEST` written last
(`src/db/checkpoint.cpp`), appends a `TRIM` entry, then `prefixTrim`s behind the *previous*
checkpoint. Every process, trimmer or not, loads `LATEST` at open and seeks the stream to `C+1`
(`bootstrap`), so the replay is bounded by one trim interval. `bootstrap` throws when the
checkpoint is ahead of the log or when the replay crosses a `TRIM` entry it did not cover: the
Corfu log dir and the bucket are one unit and must be snapshotted and restored together.
`DB::DB` builds `sstable_storage` before `log_storage` for this reason. A member whose tailer
falls below the trim mark fail-stops (`trimmed_out_`).

### Config

One flat JSON file per DB instance, parsed by `Metadata` (`metadata.h`) via `parseJSON`, which
returns `map<string,string>` — so *every* value is read as a string and arrays are literal strings
like `"[268435456, 2684354560]"`. Adding a config key means adding the parse in `Metadata`'s
constructor. Base configs live in `src/config/{local,corfu}/`.

### JNI / YCSB chain

`libOzoneDB.so` → `src/jni/jni_OzoneDBJNI.cpp` (CMake target `ozonedb_jni` → `libozonedb.so`) →
`ycsb/ozonedb/src/main/java/jni/OzoneDBJNI.java` → `ycsb/ozonedb/.../OzoneDBClient.java`. YCSB
selects the config with `-p shared_config=<path>`. Changing the JNI signature means touching all
three layers — and `jni_OzoneDBJNI.h` is javah-generated and checked in, so keep it in sync.

- The Java class must stay in package `jni`: the exported symbols are `Java_jni_OzoneDBJNI_*`, so
  renaming the package breaks the lookup at load time with no compile error.
- `OzoneDBJNI` resolves the shim itself, in order: `-Dozonedb.native.lib`, then
  `$OZONEDB_HOME/build/libozonedb.so`, then `System.loadLibrary`. Resolution lives in the class
  rather than the runners because YCSB is launched several ways — the multiproc runners build
  their own `java` command, while manual runs go through `bin/ycsb`, which builds its own.
- The shim keeps the DB in a `thread_local`, so YCSB `-threads N` opens N databases in one JVM.
  The bench runs one thread per process.

## Conventions

- `.clang-format`: Google style, `ColumnLimit: 0` (no wrapping), `int& foo` pointer/ref alignment,
  **east const** (`std::string const&`, not `const std::string&`).
- Everything lives in `namespace ozonedb`; the public API returns `Status`
  (`kSuccess`/`kFailure`/`kSealed`/`kNotFound`) rather than throwing. `Storage` base methods throw
  `std::runtime_error` for unimplemented operations — new backends override what they support.
- Non-obvious invariants (lifetime, lock ordering, aliasing) are documented in header comments; keep
  that density when changing those areas.
- The paper says **OzoneKV**. Prose and docs use that name; identifiers, library names and result
  labels keep `ozonedb`, because every plot script parses them.
