#ifndef COMPACTION_H
#define COMPACTION_H

#include "log_handler.h"
#include "metadata.h"
#include "sstable/sstable_handler.h"
#include "sstable/table_builder.h"
#include "storage.h"
#include "task_log_handler.h"
#include <atomic>
#include <random>
#include <thread>
#include <unordered_map>
#include <vector>
namespace ozonedb {
class FileMutexManager;
class EventListener;
enum class Mode;
/**
 * @brief One compaction task.
 */
class Compaction {
 public:
  TaskRecord::TaskIdentifier* task_id = nullptr;
  TableBuilder* outputBuilder = nullptr;
  bool finished = false;
  // Set when doCompactionWork gives the task up: an injected abort
  // (compaction_abort_rate), an unreadable input, or a failed output write.
  // The heartbeat thread then exits without TASK_COMPLETE, so the claim
  // lapses after the threshold and a peer reclaims the task.
  std::atomic<bool> aborted{false};
  int compaction_version = -1; // last-level compactions only
};

class CompactionWatcher {
 private:
  Metadata* metadata = nullptr;
  Storage* storage = nullptr;          // log_storage: atomic-append backend
  Storage* sstable_storage = nullptr;  // sstable_storage: object storage for SSTables
  LogHandler* task_handler = nullptr;
  LogHandler* log_handler = nullptr;
  SSTableHandler* sstable_handler = nullptr;
  MetadataLogHandler* metadata_handler = nullptr;
  TaskLogHandler* task_log_handler = nullptr;
  std::thread* compaction_thread = nullptr;
  ThreadPool* thread_pool = nullptr;
  EventListener* event_listener = nullptr;
  LRUCache* lru_cache = nullptr;
  std::string fingerprint;
  View latest_view;
  FileMutexManager* file_mutex_manager = nullptr;
  Mode mode;
  // Draws for compaction_abort_rate. Seeded per process in the constructor.
  std::mt19937_64 abort_rng_;

  Status watchForCompaction(std::atomic<bool> const* active);
  bool shouldWorkOnTask(TaskRecord::TaskIdentifier* task_id, TaskRecord*& task_record, int owner_generation);

 public:
  /**
   * @brief Construct a CompactionWatcher.
   */
  CompactionWatcher(Metadata* metadata, Storage* storage, LogHandler* log_handler, MetadataLogHandler* metadata_handler, SSTableHandler* sstable_handler, std::string fingerprint)
      : metadata(metadata), storage(storage), sstable_storage(storage), log_handler(log_handler), metadata_handler(metadata_handler), sstable_handler(sstable_handler), fingerprint(fingerprint) {
    // sstable_storage aliases storage until setSSTableStorage overrides it.
    std::seed_seq seed{std::random_device{}(), static_cast<unsigned>(std::hash<std::string>{}(fingerprint))};
    abort_rng_.seed(seed);
  }
  ~CompactionWatcher() {
  }
  void setEventListener(EventListener* event_listener) { this->event_listener = event_listener; }

  void setTaskLogHandler(TaskLogHandler* task_log_handler) { this->task_log_handler = task_log_handler; }
  // SSTable storage backend (paper §3.5). When unset, sstable_storage aliases storage.
  void setSSTableStorage(Storage* s) { this->sstable_storage = s; }
  // Block cache for write-through of compaction output. The TableBuilder
  // inserts every data block it writes, so readers skip the cold GET.
  void setLRUCache(LRUCache* cache) { this->lru_cache = cache; }
  void setFileMutexManager(FileMutexManager* file_mutex_manager) { this->file_mutex_manager = file_mutex_manager; }
  void setThreadPool(ThreadPool* thread_pool) { this->thread_pool = thread_pool; }
  void setMode(Mode mode) { this->mode = mode; }

  Status pickCompaction(Compaction*& compaction, TaskRecord*& task_record, bool& has_worked_on_compaction);
  Status startCompaction(Compaction* compaction, TaskRecord* task_record);
  /**
   * @brief Start a thread that watches for compaction tasks while the database is active.
   */
  Status startCompactionWatcher(std::atomic<bool> const* active);

  /**
   * @brief Join the compaction watcher thread.
   */
  Status stopCompactionWatcher();

 private:
  Status taskHeartbeat(Compaction* compaction, TaskRecord* task_record);
  Status initInputUnit(Compaction* compaction, std::deque<std::string>& units, int limit);
  Status initInputUnitInlastLayer(Compaction* compaction, std::deque<std::string>& units, int limit);
  Status doCompactionWork(Compaction* compaction);
};
}  // namespace ozonedb

#endif