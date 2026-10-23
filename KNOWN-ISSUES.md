# Known issues

A code audit on 2026-09-10 read the engine for correctness hazards. The items
below were confirmed by reading the code, not by reproducing them. None of
them fires in the benchmark setup of the paper (one YCSB thread per process,
a stable Corfu server, no trims mid-run), which is why they are documented
here rather than fixed before the `vldb2027` tag. File a GitHub issue if you
hit one.

Fixed already: `SIGPIPE` on a Corfu restart (every non-JVM process died with
"Broken pipe"; `sendFrame` now passes `MSG_NOSIGNAL`).

## Corfu client (`src/db/corfu/`)

1. **A write whose outcome is unknown is re-appended.** A transport error
   after the token was granted maps to `kAbortOther`, which the seam defines
   as "consumed no address". The slow path then appends the same payload
   again. Data-log duplicates are benign. A duplicated last-level COMPACT
   record is parked in the replay buffer and never applied (one leaked
   record per duplicate; it used to erase `end()`).
   `native_corfu_client.cpp` (`appendChecked`), `metadata_log_handler.cpp`.
2. **The tailer loop has no backoff and no fail-stop on error.** A server
   outage becomes a reconnect spin at 100 % CPU, and a permanent decode
   error re-reads the same addresses forever. `corfu_storage.cpp`
   (`tailerLoop`).
3. **A layout fetch failure terminates the process.** `fetchLayout` throws a
   plain `std::runtime_error` that nothing in the tailer catches.
   `corfu_reader.cpp`.
4. **A same-length overwrite by another writer is acked as ours.** `writeAt`
   treats an overwrite error with cause SAME_DATA as success without a
   read-back. Needs a sequencer re-bootstrap from a stale tail.
   `native_corfu_client.cpp`.
5. **A stale-epoch token is written under the new epoch.** `Rpc::call`
   absorbs `wrong_epoch_error` for writes, refreshes the layout, and
   re-sends log data whose token belongs to the old epoch. `corfu_rpc.cpp`.
6. **A timed-out request is re-sent on the same live socket.** For a token
   request the first grant leaks as an unwritten address (a hole every
   reader waits the hole-fill timeout on). `corfu_rpc.cpp` (`roundTrip`).
7. **DB close can block 10 to 20 s** when a hole from a dead peer sits at
   the tailer's next address. `corfu_storage.cpp` (destructor).
8. **A blocking send disables the keepalive watchdog.** `sendFrame` holds
   `write_mtx_` across a blocking `send` with no `SO_SNDTIMEO`.
   `corfu_transport.cpp`.
9. **`Rpc::epoch_` is a data race** (written under `layout_mtx_`, read
   without it). `corfu_rpc.h`.
10. **Reconnect and layout refresh sit outside the retry.** A transient
    reconnect failure fails the put without using the one retry.
    `corfu_rpc.cpp`.
11. **A lost keepalive wakeup costs 1 s per reconnect.** `close()` notifies
    the condition variable without its mutex and the wait has no
    predicate. `corfu_transport.cpp`.
12. **An RPC failure at open is reported as a bucket mismatch.** `bootstrap`
    treats any negative `streamTail()` as "checkpoint ahead of the log".
    `corfu_storage.cpp`.
13. **A refused keyed SEAL or REMOVE is invisible to the caller.** Both
    return after a stderr line; `Storage::seal` is void.
    `corfu_storage.cpp`.

## Engine

14. **The shared `index_iter` in `Table::get` is seeked without a lock**
    while the header promises thread safety. Safe at one thread per
    process. `table_reader.cpp`.
15. **The JNI shim keeps the DB in a `thread_local`.** YCSB `-threads N`
    opens N databases in one JVM. `jni_OzoneDBJNI.cpp`.
16. **Append results are dropped before destructive steps.**
    `appendToMetadataLog` and `appendToTaskLog` discard the append status;
    `doCompactionWork` then removes the inputs. `metadata_log_handler.cpp`,
    `task_log_handler.cpp`, `compaction.cpp`.
17. **Block reads ignore status** and index `block_data[0]` before checking
    for an empty buffer. `table_reader.cpp`, `block_handler.cpp`.
    (`readFilterBlock` in `filter_block.cpp` checks now; it was the crash
    site when two threads shared one read stream, item 25.)
18. **Task-log locking.** `worthAppend` reads `task_map` without a lock
    while `updateHeartbeats` mutates it; `stop_thread` is a plain bool.
    `task_log_handler.cpp`, `task_log_handler.h`. (The takeover threshold
    is `task_heartbeat_threshold_ticks` now, default 30; takeover fires
    and is tested.)
19. **A per-tick leak in the compaction watcher.** `Compaction` has no
    destructor, so `task_id` leaks on the fewer-than-two-inputs path.
    `compaction.cpp`.
20. **The JSON reader is fragile.** `trim` strips only spaces, a last array
    line without a trailing comma is truncated, and a missing file dies
    inside `std::stol` with no file name. `read_json.cpp`.
21. **The YCSB client's `insert` swallows the serialize exception** and then
    dereferences the null value; `scan` returns OK while unsupported.
    `OzoneDBClient.java`.
22. **Fully pruned levels vanish from the `[lru_cache]` diagnostic line.**
    `cache.cpp` (`printCacheStats`).
23. **`closeDB` waits for the view-update tick and any in-flight
    compaction.** `stopViewUpdate` and `stopCompactionWatcher` join their
    threads: up to 100 ms of `sleep_for`, plus however long the current
    compaction takes. A one-shot process that opens, writes once and
    closes pays that on every call. A condition-variable wait on `active`
    would make the idle case immediate. `metadata_log_handler.cpp`,
    `compaction.cpp`.
24. **A false dead-task on a Corfu tailer stall.** A hole left by a dead
    peer stalls every tailer for `corfu_hole_fill_timeout_ms` (item 7),
    and with it the replay of live peers' heartbeats, so a threshold
    below that timeout declares live tasks dead and a second writer runs
    the same compaction at the next generation (its COMPACT record is
    then parked, item 1). The Corfu configs set
    `task_heartbeat_threshold_ticks` to 200 for this reason.
    `task_log_handler.cpp`.
25. **Fixed on `port/experiment-plot`, listed so the symptom is findable.**
    `FileStorage::read` shared one `ifstream` per file across threads with
    no lock around seek + read; a reclaimed last-level task ran with no
    output version; a compaction whose input a peer had removed published
    a record covering the rest, which the replay parked after the inputs
    were deleted; a COMPACT with no outputs crashed the replay. Symptoms:
    "Failed to parse message", a SIGSEGV in `getPrefix` or
    `readFilterBlock`, keys NOT_FOUND after a multi-writer compaction
    backlog. Commits `efe016f`, `f6b10e4`, `7597d63`.
26. **`TableBuilder::fileSize()` is 8 bytes over** the bytes written
    (`offset` plus an empty block's size estimate), so `output_bytes` on
    every COMPACT record and the view's `file_size` for SSTables are 8
    bytes high. Heuristics only. `table_builder.cpp`.
27. **The task-log replay runs on the compaction thread**
    (`rollForwardTaskLogIfDue` at the top of every pick), so a compaction
    longer than `task_heartbeat_threshold_ticks` starves it and the peers'
    tick counts run past the threshold until it returns (the replay then
    resets them before the check, so nothing is declared dead, but the
    takeover of a real dead task waits that long). A replay thread in
    `TaskLogHandler` would remove the dependency. `compaction.cpp`,
    `task_log_handler.cpp`.

## Deliberate leftovers

- `disk_cache_mode = file` (whole-SSTable entries with a fill worker) lost
  the measured A/B against chunk mode at every budget below the dataset
  size. It is kept for that comparison only. Removing it halves
  `disk_cache_storage.cpp`.
- The batched-write mode of `CorfuDBStorage` (`commit_interval_ > 0`) has no
  config key and is never reached; its bookkeeping still runs on every
  append.
