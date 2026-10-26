#ifndef CORFU_STORAGE_H
#define CORFU_STORAGE_H
#ifdef OZONEDB_ENABLE_CORFU
#include "checkpoint.h"
#include "corfu_client.h"
#include "storage.h"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <list>
#include <memory>
#include <mutex>
#include <shared_mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace ozonedb {

/**
 * @brief Storage backend on one CorfuDB stream.
 *
 * All ozonedb files are packed into one shared Corfu stream. Each entry is a
 * CorfuEntry protobuf: file name, opcode (APPEND / SEAL / REMOVE / TRIM) and
 * payload. The process talks to Corfu through a CorfuClient (corfu_client.h).
 *
 * A background tailer applies every entry in address order and is the only
 * writer of the per-file buffers. Reads wait for the tailer to pass the
 * writer's last-known address (read-my-writes in one process).
 */
class CorfuDBStorage : public Storage {
 public:
  // checkpoint_store: where LogTrimmer writes checkpoints. When non-null,
  // the constructor loads <checkpoint_dir>/LATEST and replays only the
  // entries above it. When null, it replays from address 0 and throws on
  // a trimmed log.
  // fast_ack: ack a data append when the sequencer grants its token
  // (submitBatchFast) instead of after the tailer applied it. SEAL and
  // REMOVE carry their conflict key in both modes.
  CorfuDBStorage(CorfuClientOptions const& client_options,
                 std::string const& db_path,
                 Storage* checkpoint_store = nullptr,
                 std::string const& checkpoint_dir = "checkpoint",
                 bool fast_ack = true);
  ~CorfuDBStorage();

  void createDirectory(std::string name) override;
  Status append(std::string const& fileName, unsigned char* const& data, int length) override;
  Status appendNoFlush(std::string const& fileName, unsigned char* const& data, int length) override;
  Status appendInBatch(std::string const& fileName, unsigned char* const& data, int length) override;
  Status flush(std::string const& fileName) override;
  Status read(std::string const& fileName, unsigned char*& data, size_t& size) override;
  Status read(std::string const& fileName, unsigned char*& data, size_t a, size_t length) override;
  size_t size(std::string fileName) override;
  void seal(std::string fileName) override;
  bool isSealed(std::string fileName) override;
  void remove(std::string fileName) override;
  bool exist(std::string fileName) override;

  // Write mode (Metadata::corfu_sync_writes / corfu_commit_interval_ms).
  // sync (the default, interval 0): appendInBatch returns only once its
  // bytes are sequenced, so an acked put is durable. async: bytes stay in
  // cached_file_ and are submitted when the interval has elapsed since
  // the last submit, so an ack precedes durability by up to one interval.
  // flush(), drainForRead() and the destructor push what is still cached.
  void setSyncMode(bool sync) { sync_mode_ = sync; }
  void setCommitInterval(int ms) { commit_interval_ = ms; }

  // One fence per get (Metadata::linearizable_reads). sync() does one
  // sequencer query plus tailer wait and records a thread-local
  // {instance, target} token. read/size/exist/isSealed on the same thread
  // then reuse the token through fenceTargetForCaller(). The token is
  // thread-local, not process-wide. The instance field scopes it to one
  // CorfuDBStorage.
  void sync() override;
  void clearSync() override;
  bool hasSyncToken() const override { return fence_token_.instance == this; }

  long lastAppendAddressForThread() const override { return last_append_addr_; }

  // --- checkpoints and trimming ---
  //
  // Exact live state at one address, for LogTrimmer. Takes write_gate_
  // exclusively and waits for the tailer to pass this process's last
  // write. Then it copies file_buffers_ and the sealed/removed sets under
  // mtx_. covered_addr is -1 when no exact state could be taken.
  checkpoint::State takeSnapshot();
  // Corfu prefixTrim(addr) + log-unit compaction. False on a client error.
  bool prefixTrim(long addr);
  // First untrimmed address as persisted by the log units; -1 on error.
  long trimMark();
  // Tail of this stream: max of the sequencer's tail and our own last
  // write. One sequencer round-trip.
  long streamTail();
  // True once the tailer hit a trimmed address. Every write then fails
  // and every read misses.
  bool trimmedOut() const { return trimmed_out_.load(); }
  long lastAppliedAddr() const { return last_applied_addr_.load(std::memory_order_acquire); }
  // Peer APPENDs this process dropped because they were sequenced above
  // their file's SEAL (applyEntry). Every process drops the same ones.
  uint64_t droppedAboveSeal() const { return dropped_above_seal_.load(std::memory_order_relaxed); }
  uint64_t droppedAboveSealBytes() const { return dropped_above_seal_bytes_.load(std::memory_order_relaxed); }
  // Ack-path counters (submitBatchFast). fast: acked on the sequencer's
  // answer; slow: acked after the tailer wait; conflict: the sequencer
  // refused a token; spurious: a refusal whose address holds no
  // SEAL/REMOVE of the file.
  uint64_t fastAcks() const { return fast_acks_.load(std::memory_order_relaxed); }
  uint64_t slowAcks() const { return ack_wait_batches_.load(std::memory_order_relaxed); }
  uint64_t conflictAborts() const { return conflict_aborts_.load(std::memory_order_relaxed); }
  uint64_t spuriousConflicts() const { return spurious_conflicts_.load(std::memory_order_relaxed); }
  bool loadedFromCheckpoint() const { return loaded_from_checkpoint_; }

  void setRemoteAppendListener(RemoteAppendListener listener) override {
    bool cleared;
    {
      std::lock_guard<std::mutex> lk(mtx_);
      remote_listener_ = std::move(listener);
      cleared = !remote_listener_;
    }
    // When the listener is cleared, wait until the dispatcher has run
    // every event queued under the old listener. Each event captured the
    // listener's `this`; running it after destruction is a use-after-free.
    if (cleared) drainDispatchQueue();
  }
  int commit_interval_ = 0;
  bool sync_mode_ = true;

 private:
  // The Corfu client. Owned; every thread calls it directly.
  std::unique_ptr<CorfuClient> client_;

  // Per-file state. Only the tailer writes these.
  std::unordered_map<std::string, std::vector<unsigned char>> file_buffers_;
  std::unordered_set<std::string> sealed_files_;
  std::unordered_set<std::string> removed_files_;

  // Pending batched writes, per file
  std::unordered_map<std::string, std::vector<unsigned char>> cached_file_;

  // One drain of a file's cached_file_ bytes. Covers them from leaving
  // cached_file_ until they land in file_buffers_ or fail.
  //
  // Lifetime: co-owned by shared_ptr. Every thread whose bytes are in the
  // batch holds one, and pending_ holds one while the batch is in flight.
  // Never hold a bare iterator into pending_: the tailer erases pending_
  // entries when a peer REMOVEs the file.
  //
  // `done` is the single ack point. It is set once, under mtx_, by the
  // first of these:
  //   - the sequencer granted a token with the file's key in the
  //     read set (submitBatchFast)                       -> kSuccess
  //   - the tailer applied the entry at its address     -> kSuccess
  //   - the client submit failed                        -> kFailure
  //   - the file was sealed at a LOWER global address   -> kSealed
  //   - a peer REMOVE dropped the file                  -> kFailure
  //   - the tailer did not reach the address in time    -> kFailure
  // A caller acks a write only after `done` is true, and returns `result`.
  //
  // `done` means "the bytes at addr belong to fileName in every process".
  // The sequencer proves it at token time (fast path) or the tailer
  // applies the entry (slow path). The tailer is the only writer of
  // file_buffers_, own entries included, so every process holds each
  // file in address order. Reads fence on last_written_addr_.
  struct Batch {
    long addr = -1;  // global Corfu address, stamped once the outcome is known
    std::vector<unsigned char> payload;
    bool done = false;
    Status result = Status::kFailure;
  };

  // Batches handed to the client whose outcome is not known yet, per
  // file. Used only to settle in-flight batches on a REMOVE.
  std::unordered_map<std::string, std::list<std::shared_ptr<Batch>>> pending_;

  // The batch that owns the bytes in cached_file_[fn]. Created when the
  // first byte enters and moved into pending_ on drain. A thread that
  // adds bytes keeps a shared_ptr so it learns the outcome from any
  // drainer.
  std::unordered_map<std::string, std::shared_ptr<Batch>> cached_batch_;

  // Global address of a file's SEAL, once seen by the tailer or appended
  // here. Seal rule: an APPEND sequenced above this address belongs to
  // no file. The tailer applies the rule to every entry, own ones
  // included, in address order (applyEntry).
  std::unordered_map<std::string, long> sealed_at_addr_;

  // The write gate. file_buffers_ under mtx_ is the state at
  // last_applied_addr_, because only the tailer writes it, in address
  // order. The gate keeps local submit paths out while takeSnapshot waits
  // for the tailer to pass last_written_addr_ and copies.
  //
  // Every submit path holds the gate SHARED until its outcome is known
  // (append, appendInBatch, flush, drainForRead, seal, remove).
  // takeSnapshot holds it EXCLUSIVE. Lock order: write_gate_ before
  // mtx_. Nothing that holds mtx_ waits for the gate. Never take the gate
  // twice on one thread: submitBatch and takeCachedBatchLocked assume the
  // caller holds it.
  std::shared_mutex write_gate_;
  // Set by failStop() when the tailer hit a trimmed address. Sticky.
  std::atomic<bool> trimmed_out_{false};
  bool loaded_from_checkpoint_ = false;
  // Highest X of a TRIM entry this process applied; -1 when none.
  // Guarded by mtx_. bootstrap compares it with its replay start address.
  long trim_marker_addr_ = -1;

  std::mutex mtx_;
  std::condition_variable batch_flushed_cv_;
  std::condition_variable tailer_cv_;
  std::chrono::system_clock::time_point last_commited_time_;

  std::atomic<long> last_applied_addr_{-1};
  std::atomic<long> last_written_addr_{-1};

  // Ack-wait counters (submitBatch): how long acks waited for the tailer,
  // and how many batches sat above a SEAL learned during that wait.
  // Printed once at teardown.
  std::atomic<uint64_t> ack_wait_batches_{0};
  std::atomic<uint64_t> ack_wait_us_total_{0};
  std::atomic<uint64_t> ack_wait_us_max_{0};
  std::atomic<uint64_t> sealed_after_wait_{0};
  std::atomic<uint64_t> dropped_above_seal_{0};
  std::atomic<uint64_t> dropped_above_seal_bytes_{0};
  // Fast-path counters (submitBatchFast). slow_path_* count the sequencer
  // answers that sent a batch to the slow path, by abort type.
  bool fast_ack_ = true;
  std::atomic<uint64_t> fast_acks_{0};
  std::atomic<uint64_t> conflict_aborts_{0};
  std::atomic<uint64_t> spurious_conflicts_{0};
  std::atomic<uint64_t> slow_path_newseq_{0};
  std::atomic<uint64_t> slow_path_overflow_{0};
  std::atomic<uint64_t> slow_path_trim_{0};
  std::atomic<uint64_t> slow_path_other_{0};
  std::atomic<uint64_t> slow_path_stalled_{0};

  // Random id that tags every APPEND this process writes. The tailer
  // applies own and peer entries alike; the id only decides whether the
  // remote-append listener fires.
  uint64_t client_id_ = 0;

  std::thread tailer_thread_;
  std::atomic<bool> running_{false};

  // Called for every applied entry that did not come from this process
  // (remote APPENDs and every REMOVE). Installed by LogHandler. Runs on
  // dispatch_thread_; the tailer never calls it synchronously.
  RemoteAppendListener remote_listener_;

  // Queue of remote events for the dispatch thread. The listener takes
  // LRUCache::mutex, which a foreground thread can hold while it fences
  // on this tailer. Running the listener on the tailer would deadlock,
  // so a dedicated thread drains this queue. The queue is unbounded: a
  // full bounded queue would block the tailer and reintroduce the cycle.
  struct RemoteEvent {
    RemoteOp op;
    std::string file_name;
    std::vector<unsigned char> payload;
    RemoteAppendListener listener;
    long addr = -1;
  };
  std::mutex dispatch_mtx_;
  std::condition_variable dispatch_cv_;
  // Signaled by the dispatcher when the queue is empty after an event.
  // drainDispatchQueue() waits on it.
  std::condition_variable drain_cv_;
  std::deque<RemoteEvent> dispatch_queue_;
  // True while the dispatcher runs a listener for a popped event.
  // drainDispatchQueue() waits on this as well as on an empty queue.
  bool dispatch_in_flight_ = false;
  std::thread dispatch_thread_;

  // One CorfuEntry, serialized. Shared by both append flavours.
  std::string serializeEntry(std::string const& file_name, int op,
                             unsigned char const* data, int length) const;
  // Plain append: token without conflict keys. The outcome is known only
  // once the tailer passed its address.
  long appendEntry(std::string const& file_name, int op,
                   unsigned char const* data, int length);
  // Outcome of CorfuClient::appendChecked (see corfu_client.h).
  using CheckedAppend = CorfuClient::CheckedAppend;
  static constexpr long kAbortConflict = CorfuClient::kAbortConflict;
  static constexpr long kAbortNewSequencer = CorfuClient::kAbortNewSequencer;
  static constexpr long kAbortSeqOverflow = CorfuClient::kAbortSeqOverflow;
  static constexpr long kAbortSeqTrim = CorfuClient::kAbortSeqTrim;
  static constexpr long kAbortOther = CorfuClient::kAbortOther;
  // The conflict key of a file is its name; names are never reused.
  // read_key puts it in the read set at `snapshot`; write_key puts it in
  // the write set. See CorfuClient::appendChecked.
  CheckedAppend appendChecked(std::string const& file_name, int op,
                              unsigned char const* data, int length,
                              long snapshot, bool read_key, bool write_key);
  // Append a payload-less entry (SEAL, REMOVE) with the file's key in the
  // write set. Retries a refused snapshot with the global tail. Returns
  // the address, or -1 when the entry was not appended.
  long appendKeyed(std::string const& file_name, int op);
  // Raise last_written_addr_ to addr (monotonic).
  void publishWrittenAddr(long addr);
  // Apply one stream entry at global address addr to local state. Runs
  // on the tailer thread, or on the constructor's thread during replay.
  // The only writer of file_buffers_, in address order.
  bool applyEntry(long addr, unsigned char const* data, size_t len);
  // Replay from the poll view's position to the tail. kTrimmed when the
  // view hit a trimmed address; kError on a client failure (the state is
  // partial).
  enum class DrainResult { kOk,
                           kTrimmed,
                           kError };
  DrainResult drainInitialEntries();
  // Constructor-time replay: newest checkpoint when a store is given,
  // seek to covered_addr + 1, drain, then check the trim mark. Throws
  // when the log is trimmed past what this process could load.
  void bootstrap(Storage* checkpoint_store, std::string const& checkpoint_dir);
  void resetStateLocked();
  void restoreSnapshot(checkpoint::State&& state);
  bool seekPollView(long addr);
  // Append the TRIM entry for X. Returns its address, -1 on failure.
  long appendTrimMarker(long addr);
  // The tailer fell below the trim mark: mark the storage dead, settle
  // every waiting writer with kFailure, wake every waiter.
  void failStop(char const* why);
  void tailerLoop();
  void dispatchLoop();
  void drainDispatchQueue();
  long globalFenceTarget();
  void waitForTailerLocked(std::unique_lock<std::mutex>& lock, long target);
  // Same wait with an explicit bound; true when the tailer reached target.
  bool waitForTailerLocked(std::unique_lock<std::mutex>& lock, long target,
                           std::chrono::milliseconds timeout);
  // True when a SEAL for fileName is known at an address below addr.
  bool sealedBelowLocked(std::string const& fileName, long addr) const;

  // --- batch helpers (see struct Batch) ---
  // All *Locked helpers require mtx_ held by the caller.
  //
  // The batch owning cached_file_[fileName], created on first use.
  std::shared_ptr<Batch>& cachedBatchLocked(std::string const& fileName);
  // Move cached_file_[fileName] into its batch and hand it back for
  // submission. Returns null when the file has no buffered bytes.
  std::shared_ptr<Batch> takeCachedBatchLocked(std::string const& fileName);
  // Settle a batch that will never be spliced and unblock the queue
  // behind it.
  void finishBatchLocked(std::string const& fileName,
                         std::shared_ptr<Batch> const& batch, Status result);
  // Client submit, then wait until the tailer passed the new address and
  // read the outcome. Call with mtx_ NOT held and write_gate_ held
  // shared. Sets batch->done before it returns, and returns batch->result.
  Status submitBatch(std::string const& fileName, std::shared_ptr<Batch> const& batch);
  // Fast path of submitBatch: keyed append, acked on the sequencer's
  // answer. True when the batch is settled; false when the sequencer
  // could not answer and submitBatch must take the slow path. Same
  // locking contract as submitBatch.
  bool submitBatchFast(std::string const& fileName,
                       std::shared_ptr<Batch> const& batch);
  // Block until `batch` is settled by whichever thread owns it, and
  // return its outcome. Takes mtx_ itself, so the caller must not hold it.
  Status awaitBatch(std::shared_ptr<Batch> const& batch);
  // Push every not-yet-sequenced byte of one file onto the log and wait
  // for the splice. read()/size() then serve file_buffers_ alone.
  void drainForRead(std::string const& fileName);

  // The calling thread's sync() token. Static thread_local; the instance
  // pointer scopes it to one storage object.
  struct FenceToken {
    CorfuDBStorage const* instance = nullptr;
    long target = -1;
  };
  static thread_local FenceToken fence_token_;

  // Global address of this thread's last successful append. Set by
  // append/appendInBatch/flush; read by LogHandler through
  // lastAppendAddressForThread().
  static thread_local long last_append_addr_;
  // Fence target for the current fenced read: the token's target when
  // this thread holds one for this instance, else a fresh
  // globalFenceTarget() sample.
  long fenceTargetForCaller();
};

}  // namespace ozonedb
#endif  // OZONEDB_ENABLE_CORFU
#endif  // CORFU_STORAGE_H
