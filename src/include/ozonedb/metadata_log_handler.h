
#ifndef METADATA_LOG_HANDLER_H
#define METADATA_LOG_HANDLER_H
#include "cache.h"
#include "metadata.h"
#include "protobuf/sstable.pb.h"
#include "protobuf_serializer.h"
#include "storage.h"
#include <atomic>
#include <functional>
#include <map>
#include <memory>
#include <queue>
#include <string>
#include <vector>

namespace ozonedb {
class EventListener;
class View {
 public:
  std::unordered_map<std::string, std::deque<std::string>> storage_layout;
  std::unordered_map<std::string, std::pair<std::string, std::string>> key_range;
  std::unordered_map<std::string, size_t> file_size;
  std::string current_log_tail;
  size_t tail_size = 0;

  // All accessors are const and never insert. A View is shared behind a
  // shared_ptr<View const> and read from many threads without a copy.
  int getFileNumber(std::string const& prefix) const;

  // Returns a reference into the map, or a static empty deque on a miss.
  // The reference is valid while the caller keeps the View alive.
  std::deque<std::string> const& getWithPrefix(std::string const& prefix) const;

  std::pair<std::string, std::string> getKeyRange(std::string const& file_name) const;

  size_t getFileSize(std::string const& file_name) const;

  size_t getTailSize() const { return tail_size; }

  std::string const& getCurrentLogTail() const { return current_log_tail; }
};

class MetadataLogHandler {
 private:
  size_t offset;
  std::string active_unit;
  Storage* storage = nullptr;
  LRUCache* lru_cache = nullptr;
  // Called with each COMPACT input after the LRU cache dropped it, outside
  // view_mutex. The DB wires the disk tier here.
  std::function<void(std::string const&)> sstable_removed_listener_;
  std::thread* update_view_thread = nullptr;
  View latest_view;
  // Immutable snapshot of latest_view. publishSnapshotLocked swaps it
  // after each mutation with the std::atomic free functions on shared_ptr.
  // Readers load it instead of copying latest_view.
  std::shared_ptr<View const> latest_snapshot_;
  // Length of the contiguous metadata-log prefix applied into latest_view.
  // Guarded by view_mutex. readMetadataLog commits `offset` (guarded by
  // read_mutex) before its caller applies the batch. syncView therefore
  // gates on this value, not on `offset`. Batches can apply out of order;
  // applied_gaps_ parks a range until the prefix below it is applied.
  size_t applied_offset_ = 0;
  std::map<size_t, size_t> applied_gaps_;
  // Record that [start, end) is applied into latest_view and advance
  // applied_offset_ over any parked ranges that are now contiguous.
  // Caller holds view_mutex as a unique_lock.
  void markAppliedLocked(size_t start, size_t end);
  Metadata* metadata = nullptr;
  std::shared_mutex view_mutex;
  std::shared_mutex read_mutex;
  std::unordered_map<std::string, std::priority_queue<std::pair<int, OperationRecord*>, std::vector<std::pair<int, OperationRecord*>>, std::greater<>>> buffer;

  // Read the OperationRecords appended since the last read. When set,
  // observed_size receives the log length this call saw, 0 when the log
  // does not exist. batch_start receives the offset the batch starts at.
  // On Corfu size() is fenced, so the first observation is syncView's fence.
  std::vector<OperationRecord*> readMetadataLog(size_t* observed_size = nullptr,
                                                size_t* batch_start = nullptr);
  std::string rollforwardSingleOperationRecord(OperationRecord* record);
  // Apply every buffered record for `layer` that can apply now.
  // Caller holds view_mutex as a unique_lock.
  void drainBuffer(std::string const& layer);
  // Refresh latest_view.tail_size. storage->size() runs without view_mutex,
  // which keeps the Corfu fence out of the critical section.
  void refreshTailSizeUnlocked();
  // Publish a new shared_ptr<View const> snapshot of latest_view.
  // Caller holds view_mutex as a unique_lock.
  void publishSnapshotLocked();

  // Cache work owed for COMPACT records applied to the View. The cache
  // mutex must never be taken under view_mutex: the Corfu tailer's listener
  // takes the cache mutex. A foreground thread may hold it while it
  // fences on the tailer. The COMPACT branches queue an event under
  // view_mutex; the callers drain the queue after their lock scope ends.
  // The atomic flag lets syncView skip the unique_lock when the queue is empty.
  struct CompactionEvent {
    std::vector<std::string> inputs;
    std::vector<std::string> outputs;
    std::vector<size_t> output_bytes;
    int dest_level = 0;
  };
  std::vector<CompactionEvent> pending_cache_events_;
  std::atomic<bool> has_pending_cache_events_{false};
  void queueCacheEventLocked(OperationRecord const* record);
  void drainCacheEvents();

 public:
  EventListener* event_listener = nullptr;
  MetadataLogHandler(std::string metadata_log, Storage* storage) {
    this->offset = 0;
    this->storage = storage;
    this->active_unit = metadata_log;
  };
  void setEventListener(EventListener* event_listener) { this->event_listener = event_listener; }
  
  void setLRUCache(LRUCache* lru_cache) { this->lru_cache = lru_cache; }

  // Called with each COMPACT input after the LRU cache dropped it, outside
  // view_mutex. The DB wires the disk tier here.
  void setSSTableRemovedListener(std::function<void(std::string const&)> listener) { this->sstable_removed_listener_ = std::move(listener); }

  void setMetadata(Metadata* metadata) { this->metadata = metadata; }

  ~MetadataLogHandler(){};
  std::shared_mutex& getViewMutex() { return view_mutex; }

  // Background thread: replays the metadata log every 100 ms.
  void rollForwardMetadataLogPeriodically(std::atomic<bool> const* active);

  // Synchronous replay that returns a copy of the View. The compaction and
  // put paths use it.
  View rollForwardMetadataLog();

  // Synchronous replay with no View copy: applies every LOGCREATE/COMPACT
  // the storage layer can see now. With no fence token on the calling
  // thread, readMetadataLog fences on the global tail; under a
  // Storage::SyncScope it reuses the caller's fence and reads local state.
  // Either way the View then covers everything sequenced before the
  // caller's fence point. This is the per-get hook for linearizable_reads.
  // It skips the tail-size refresh; the strict read path re-fences the
  // tail itself in checkReadMoreLog.
  //
  // Loops until applied_offset_ covers the log length sampled at entry:
  // a caller can lose its batch to a concurrent readMetadataLog winner
  // while the View is still short of its fence. Returns the applied
  // offset the published View reflects. DB::get compares it with a
  // post-scan fenced log length to detect a compaction that landed mid-scan.
  size_t syncView();

  void initSSTMetadata();

  void getLatestView(View& view);

  // Atomic load of the latest View snapshot. The returned shared_ptr keeps
  // the View alive for the caller. Read-only hot paths use this: no lock,
  // no copy.
  std::shared_ptr<View const> latestViewSnapshot() const;

  // Append an OperationRecord to the metadata log.
  void appendToMetadataLog(OperationRecord const& record);

  // Start a thread to roll forward the metadata log periodically
  Status startViewUpdate(std::atomic<bool> const* active);

  Status stopViewUpdate();
};
}  // namespace ozonedb
#endif