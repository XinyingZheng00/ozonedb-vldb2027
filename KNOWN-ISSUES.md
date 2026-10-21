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
   record reaches `level_layout.erase(it)` with `it == end()`.
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
17. **Filter reads ignore status** and index `block_data[0]` before checking
    for an empty buffer. `table_reader.cpp`, `block_handler.cpp`,
    `filter_block.cpp`.
18. **Task-log locking.** `worthAppend` reads `task_map` without a lock
    while `updateHeartbeats` mutates it; `stop_thread` is a plain bool.
    Heartbeats are appended every 100 ms and the takeover threshold is
    10,000,000 heartbeats, so dead-task takeover never fires.
    `task_log_handler.cpp`, `task_log_handler.h`.
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

## Deliberate leftovers

- `disk_cache_mode = file` (whole-SSTable entries with a fill worker) lost
  the measured A/B against chunk mode at every budget below the dataset
  size. It is kept for that comparison only. Removing it halves
  `disk_cache_storage.cpp`.
- The batched-write mode of `CorfuDBStorage` (`commit_interval_ > 0`) has no
  config key and is never reached; its bookkeeping still runs on every
  append.
- The tests hard-code `/tank/test/` as their data root.
