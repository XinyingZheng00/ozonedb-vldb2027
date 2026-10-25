#ifndef OZONEDB_CACHE_H
#define OZONEDB_CACHE_H
#include "sstable/block_handler.h"
#include "sstable/table_reader.h"
#include "storage.h"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <future>
#include <list>
#include <memory>
#include <shared_mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <mutex>
namespace ozonedb {

class View;
class FileMutexManager;
class LRUCache {
 private:
  struct CacheEntry {
    // Log and SSTable block records. A Record is shared_ptr-owned. A
    // concurrent reader holds it past invalidateLogFile, a
    // putLogRecordSingle overwrite, or LRU eviction. The cache holds the
    // canonical reference; a borrower holds its own until it releases.
    std::unordered_map<std::string, std::shared_ptr<Record>> records;
    std::unordered_map<std::string, std::unordered_map<std::string, std::shared_ptr<Record>>*> block_records;  // index_value_for_block -> records
    std::unordered_map<std::string, size_t> block_size;                                       // index_value_for_block -> tail
    std::unordered_map<std::string, std::list<std::pair<std::string, std::string>>::iterator> lru_itr;
    // Blocks the warm worker published that no reader has read yet. A get()
    // on a marked block counts one warm hit and clears the mark. Evict and
    // invalidate drop the mark with the block.
    std::unordered_set<std::string> warmed_blocks;
    // Set by TableBuilder::finish through markSSTableComplete: every block
    // of the file went through the write-through. The warm worker skips it.
    bool complete = false;

    Table* table = nullptr;
    size_t offset = 0;    // records are cached up to this offset
    bool sealed = false;  // the file is sealed and fully cached
    CacheEntry() {}

    // constructor for records
    CacheEntry(std::unordered_map<std::string, std::shared_ptr<Record>> records, size_t offset, bool sealed)
        : records(std::move(records)), table(nullptr), offset(offset), sealed(sealed) {}
    // constructor for table
    CacheEntry(Table* table) : table(table), offset(), sealed(true) {}
  };
  // Storage for log files and for SSTable files. The two pointers alias in
  // a single-backend config. With sstable_backend set they differ, and each
  // method reads from the right one (paper §3.5).
  Storage* log_storage_ = nullptr;
  Storage* sstable_storage_ = nullptr;
  size_t capacity = 33554432;
  size_t current_size = 0;
  std::shared_mutex mutex;  // guards file_to_entry_map and lru_list
  std::unordered_map<std::string, CacheEntry> file_to_entry_map;
  std::list<std::pair<std::string, std::string>> lru_list;
  FileMutexManager* file_mutex_manager = nullptr;
  View const* latest_view = nullptr;

  // Fenced size of each sealed log file, memoized.
  //
  // The View's size for a sealed log is the sealing writer's unfenced
  // OperationRecord::sealed_input_bytes. Peer records appended right before
  // the seal sit above that bound. The bound must come from a fenced
  // storage->size(). A sealed file never grows, so one fence per file per
  // process is enough. Guarded by sealed_size_mtx_, never by `mutex`:
  // storage->size() fences on the tailer, and the tailer's listener takes
  // `mutex`.
  std::mutex sealed_size_mtx_;
  std::unordered_map<std::string, size_t> sealed_size_;
  size_t sealedLogSize(std::string const& file_name);

  // Single-flight for cold block and table loads: one leader loads, the
  // others wait on its future.
  std::unordered_map<std::string, std::shared_future<void>> block_inflight_;
  std::unordered_map<std::string, std::shared_future<void>> table_inflight_;

  // Tables of removed files, kept alive for a grace period. getSSTable
  // hands out a raw Table*. Table::get runs on it without `mutex`.
  // An invalidate must not delete a Table under a reader in flight.
  // Entries older than kRetiredTableGraceMs are freed on the next
  // invalidate and in the destructor.
  static constexpr int64_t kRetiredTableGraceMs = 30000;
  std::deque<std::pair<std::chrono::steady_clock::time_point, Table*>> retired_tables_;
  // Deletes retired Tables past the grace period. Caller holds `mutex`
  // exclusively.
  void freeRetiredLocked(std::chrono::steady_clock::time_point now);

  // Cache counters. printCacheStats prints them at DB close.
  std::atomic<uint64_t> sstable_cache_hits_{0};
  std::atomic<uint64_t> sstable_cache_misses_{0};
  // Reads of a file the View listed that failed at the storage layer:
  // readDataLog, getSSTable or readDataBlocks. The normal cause is a peer
  // compaction that removed the file after the reader took its View.
  // DB::get samples this counter around its scan and retries on a fresh
  // View when it moved. It is a counter, not a per-call flag: a
  // single-flight follower never sees the leader's failure.
  std::atomic<uint64_t> read_failures_{0};
  // Hits and misses per SSTable level. Slot = the level parsed from
  // "sstable<L>/<name>"; slot 0 = any other name.
  static constexpr int kLevelSlots = 16;
  std::atomic<uint64_t> level_hits_[kLevelSlots] = {};
  std::atomic<uint64_t> level_misses_[kLevelSlots] = {};
  // Probe outcomes per level, one probe per candidate file in Table::get.
  // pruned = a bloom filter said no before any block read. absent = a
  // block was read and the key was not in it. probes - pruned - absent =
  // found.
  std::atomic<uint64_t> level_probes_[kLevelSlots] = {};
  std::atomic<uint64_t> level_pruned_[kLevelSlots] = {};
  std::atomic<uint64_t> level_absent_[kLevelSlots] = {};
  // Warm-worker counters. All zero until the worker exists.
  std::atomic<uint64_t> warm_files_{0};
  std::atomic<uint64_t> warm_blocks_{0};
  std::atomic<uint64_t> warm_bytes_{0};
  std::atomic<uint64_t> warm_hits_{0};
  std::atomic<uint64_t> warm_skipped_disabled_{0};
  std::atomic<uint64_t> warm_skipped_level_{0};
  std::atomic<uint64_t> warm_skipped_budget_{0};
  std::atomic<uint64_t> warm_skipped_affinity_{0};
  std::atomic<uint64_t> warm_skipped_gone_{0};
  std::atomic<uint64_t> warm_skipped_built_{0};
  std::atomic<uint64_t> warm_dropped_{0};

  // Moves a block to the front of the LRU list.
  void updateLRU(std::string const& file_name, std::string const& index_value);

  // Evicts least recently used blocks until current_size <= capacity.
  void evict();

 public:
  // Level of an SSTable name "sstable<L>/<name>"; 0 for any other name or
  // a level outside [1, kLevelSlots). A plain scan: it runs on every read.
  static int levelSlot(std::string const& file_name);

  // Number of failed file reads so far (see read_failures_).
  uint64_t readFailures() const { return read_failures_.load(std::memory_order_acquire); }

  // ---- Warm worker ----
  //
  // When a COMPACT is applied to this process's View, the outputs are
  // offered to one worker thread. The worker reads each accepted file in
  // ranged chunks (Table::warm) and publishes its blocks. The policy runs
  // at enqueue time; every rule has a counter.
  //
  // Lock order: the worker takes `mutex` (getSSTable, putSSTableRecords)
  // and warm_mtx_ separately, never nested. onCompactionApplied is called
  // with no cache lock held and outside view_mutex.
  struct WarmPolicy {
    bool enabled = false;
    int max_level = 1;               // warm outputs of level <= this
    double max_fraction = 0.25;      // output bytes <= max_fraction * capacity
    size_t min_input_blocks = 1;     // cached input blocks needed (affinity)
    size_t read_bytes = 64u << 20;   // ranged-read chunk (compaction_read_bytes)
    size_t max_queue = 64;           // oldest entries beyond this are dropped
  };
  enum class WarmSkip { kNone, kDisabled, kLevel, kBudget, kAffinity };
  // The policy as a pure function. Rule order: enabled, level, budget,
  // affinity.
  static WarmSkip warmDecision(WarmPolicy const& policy, int level, size_t bytes, size_t capacity, size_t input_blocks);
  // Set before startWarmWorker; not re-read under a lock afterwards.
  void setWarmPolicy(WarmPolicy const& policy);
  WarmPolicy const& warmPolicy() const { return warm_policy_; }
  // Tells the worker whether a queued file is still in the View when it
  // reaches the front. Unset means every file is live.
  void setLiveFileCheck(std::function<bool(std::string const&)> check);
  // COMPACT outputs, with the sizes the record carries and the number of
  // their inputs' blocks cached here (summed invalidateSSTable returns).
  // Applies the policy and queues the accepted outputs. Before
  // startWarmWorker every output counts as disabled.
  //
  // log_inputs: the inputs are log files, so cached_input_blocks is 0.
  // The affinity signal is then this process's lookups on the destination
  // level since the previous log-input event.
  void onCompactionApplied(std::vector<std::string> const& outputs, std::vector<size_t> const& output_bytes, int dest_level, size_t cached_input_blocks, bool log_inputs = false);
  void startWarmWorker();
  // Joins the worker and drops queued files. Idempotent; the destructor
  // calls it.
  void stopWarmWorker();
  // Testing: blocks until the queue is empty and no file is in flight.
  void waitWarmIdle();
  // TableBuilder::finish: every block of the file is already published.
  void markSSTableComplete(std::string const& file_name);

  // One consistent reading of every counter, taken under `mutex`. dead_*
  // cover blocks whose file is not in the View; they must be 0.
  // printCacheStats formats exactly this.
  struct Stats {
    uint64_t hits = 0;
    uint64_t misses = 0;
    uint64_t level_hits[kLevelSlots] = {};
    uint64_t level_misses[kLevelSlots] = {};
    uint64_t level_probes[kLevelSlots] = {};
    uint64_t level_pruned[kLevelSlots] = {};
    uint64_t level_absent[kLevelSlots] = {};
    size_t capacity = 0;
    size_t current_size = 0;
    size_t files = 0;
    size_t sstable_files = 0;  // entries that hold a Table* or blocks
    size_t dead_bytes = 0;
    size_t dead_blocks = 0;
    size_t dead_files = 0;
    size_t retired_tables = 0;
    uint64_t warm_files = 0;
    uint64_t warm_blocks = 0;
    uint64_t warm_bytes = 0;
    uint64_t warm_hits = 0;
    uint64_t warm_skipped_disabled = 0;
    uint64_t warm_skipped_level = 0;
    uint64_t warm_skipped_budget = 0;
    uint64_t warm_skipped_affinity = 0;
    uint64_t warm_skipped_gone = 0;
    uint64_t warm_skipped_built = 0;
    uint64_t warm_dropped = 0;
  };
  Stats stats();

  // Single-storage constructor: log and SSTable reads use the same backend.
  LRUCache(size_t capacity, Storage* storage)
      : log_storage_(storage), sstable_storage_(storage), capacity(capacity) {}

  // Split-storage constructor: log reads use log_storage, SSTable reads
  // use sstable_storage. Used when the config sets sstable_backend.
  LRUCache(size_t capacity, Storage* log_storage, Storage* sstable_storage)
      : log_storage_(log_storage),
        sstable_storage_(sstable_storage),
        capacity(capacity) {}

  ~LRUCache() {
    stopWarmWorker();
    for (auto& entry : file_to_entry_map) {
      // Records are shared_ptr. Each block_records value is a heap map the
      // cache owns.
      for (auto block : entry.second.block_records) {
        delete block.second;
      }
      if (entry.second.table != nullptr) {
        delete entry.second.table;
      }
    }
    for (auto& retired : retired_tables_) {
      delete retired.second;
    }
  }
  // Testing only.
  std::unordered_map<std::string, CacheEntry> getCacheMap() {
    return file_to_entry_map;
  }

  void setFileMutexManager(FileMutexManager* file_mutex_manager) {
    this->file_mutex_manager = file_mutex_manager;
  }

  void checkReadMoreLog(std::string const& file_name, bool& read_more, size_t& cached_offset, size_t& size);
  void readDataLog(std::string const& file_name, size_t cached_offset, size_t size);
  void getSSTable(std::string const& file_name, Table*& table);
  void needReadBlock(std::string const& file_name, bool& read_more, std::string const& index_value);
  // Probe accounting for Table::get. kind 0 = a probe of this file
  // started, 1 = a filter pruned it, 2 = a block was read and the key was
  // not in it. See level_probes_.
  void noteProbe(std::string const& file_name, int kind);
  // caller_table: the Table the caller reads through (Table::get passes
  // `this`). Used when the entry has no Table*. Either the compaction
  // write-through created the entry before any read, or a REMOVE erased
  // it after needReadBlock. An unreadable block is a miss, never a crash.
  void readDataBlocks(std::string const& file_name, std::string const& index_value, Table* caller_table = nullptr);

  // SSTable variant. Returns a shared_ptr so the Record outlives a
  // concurrent evict(). Updates LRU order on hit.
  void get(std::string const& file_name, std::string const& key, std::shared_ptr<Record>& record, std::string const& index_value = "");
  // Log variant. Returns a shared_ptr so the Record outlives a concurrent
  // invalidateLogFile or a same-key overwrite. Used by LogHandler reads
  // and the LogKeyIndex.
  void getLog(std::string const& file_name, std::string const& key, std::shared_ptr<Record>& record);
  // Merges records into the file's entry and updates offset and sealed.
  void putLogRecords(std::string const& key, std::unordered_map<std::string, std::shared_ptr<Record>> records, size_t offset, bool sealed);
  // Inserts or overwrites one Record of a log file. The cache takes
  // shared ownership. A prior Record at the same key is released; it
  // frees when the last borrower releases it.
  void putLogRecordSingle(std::string const& file_name, std::shared_ptr<Record> record);
  // Drops a log file's entry. Records free when the last borrower (a
  // reader, the LogKeyIndex) releases them. Called when a log file is
  // compacted away.
  void invalidateLogFile(std::string const& file_name);
  // Drops an SSTable a COMPACT removed from the View. Every cached block
  // leaves the LRU list and the byte budget. The Table* is retired (see
  // retired_tables_). The entry is erased. Returns the number of blocks
  // that were cached; the warm policy reads it as this process's interest
  // in the region. A log entry is left alone:
  // LogHandler::invalidateCompactedLog owns those. Never call this under
  // view_mutex.
  size_t invalidateSSTable(std::string const& file_name);
  // Copies the file's records map into `out`. Cache and `out` co-own
  // each Record. LogKeyIndex::warm uses it at startup.
  void snapshotLogFileRecords(std::string const& file_name,
                              std::unordered_map<std::string, std::shared_ptr<Record>>& out);
  void putSSTableMeta(std::string const& key, Table* table);
  // Publishes one block. The cache takes ownership of `records`; a
  // duplicate is dropped. `warmed` marks the block for the warm-hit
  // counter.
  void putSSTableRecords(std::string const& key, std::unordered_map<std::string, std::shared_ptr<Record>>* records, std::string const& index_value, size_t size, bool warmed = false);

  void setLatestView(View const* view) {
    latest_view = view;
  }

  // Prints the counters to stderr at DB close. Two lines: `[lru_cache]
  // sstable hits=...`, which bench/scripts/extract_cost_coefficients.py
  // parses, and `[lru_cache] levels ...` with the per-level, dead-block
  // and warm counters.
  void printCacheStats();

 private:
  // Warm worker state. Declared after the public block because WarmPolicy
  // is a nested type.
  WarmPolicy warm_policy_;
  std::function<bool(std::string const&)> live_file_check_;
  std::mutex warm_mtx_;
  std::condition_variable warm_cv_;
  std::deque<std::string> warm_queue_;
  bool warm_started_ = false;
  bool warm_stop_ = false;
  bool warm_busy_ = false;
  // Per-level lookup count at the previous log-input compaction event.
  // Guarded by warm_mtx_; see onCompactionApplied.
  uint64_t level_lookups_at_last_log_event_[kLevelSlots] = {};
  std::thread warm_thread_;
  void warmLoop();
  void warmOne(std::string const& file_name);
};
}  // namespace ozonedb
#endif