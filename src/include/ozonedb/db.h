#ifndef DB_H
#define DB_H

#include "cache.h"
#include "compaction.h"
#include "log_handler.h"
#include "protobuf/record.pb.h"
#include "sstable/sstable_handler.h"
#include <memory>
#include <thread>

namespace ozonedb {
class FileMutexManager {
  std::unordered_map<std::string, std::shared_mutex> file_to_mutex_map;
  std::shared_mutex map_mutex;

 public:
  std::shared_mutex& getMutexForFile(std::string const& filename) {
    std::unique_lock<std::shared_mutex> lock(map_mutex);
    return file_to_mutex_map[filename];
  }
};

class EventListener {
 public:
  virtual void onLogCompactionStart(){};
  virtual void onLogCompactionCompletion(int time){};
  virtual void onSSTableCompactionStart(){};
  virtual void onSSTableCompactionCompletion(int time, int level){};
  virtual void onViewUpdate(){};
  virtual void onNewTail(){};
};

enum class Mode {
  Singleton,
  MultipleProcesses,
};
class LogTrimmer;
class DiskCacheStorage;
class DB {
 private:
  /**
   * @brief State of the database.
   */
  std::atomic<bool> active;
  Mode mode;

  /**
   * @brief Storage modules of the database.
   *
   * log_storage holds the data log, the metadata log and the task log.
   * sstable_storage holds SSTables (paper §3.5). Without a sstable_backend
   * config key the two pointers alias. The destructor guards the double-free.
   */
  Storage* log_storage = nullptr;
  Storage* sstable_storage = nullptr;
  // The disk tier in front of sstable_storage when disk_cache_dir is set.
  // Then sstable_storage is this object and owns the backing store. Non-owning.
  DiskCacheStorage* disk_cache = nullptr;
  Metadata* metadata;
  CompactionWatcher* watcher = nullptr;
  // Set only when Metadata::log_trim_enabled and the log backend is Corfu.
  // closeDB stops it after its final cycle. ~DB deletes it first because
  // it uses both stores.
  LogTrimmer* trimmer = nullptr;
  std::string fingerprint;
  // Immutable snapshot of the View. DB::get refreshes it once per call with
  // std::atomic_load. The raw pointer each child handler stores
  // (setLatestView) aliases into this snapshot. Do not deep-copy the View.
  std::shared_ptr<View const> latest_view_snapshot;

  /**
   * @brief Handlers for reads and writes.
   */
  MetadataLogHandler* metadata_log = nullptr;
  LogHandler* log_handler = nullptr;
  SSTableHandler* sstable_handler = nullptr;
  LRUCache* lru_cache = nullptr;
  FileMutexManager* file_mutex_manager = nullptr;
  ThreadPool* thread_pool = nullptr;
  EventListener* event_listener = nullptr;

  DB(std::string const& shared_config_path);
  ~DB();

 public:
  // Set the event listener.
  void setEventListener(EventListener* event_listener) {
    this->event_listener = event_listener;
    this->watcher->setEventListener(event_listener);
    this->metadata_log->setEventListener(event_listener);
  }

  /**
   * @brief Open the database.
   *
   * Reads the config, creates the handlers and starts the compaction watcher.
   *
   * @param db the database object
   * @param shared_config_path path to the shared config file
   * @return Status
   */
  static Status openDB(DB*& db, std::string const& shared_config_path);

  /**
   * @brief Close the database.
   *
   * @param db the database object
   * @return Status
   */
  static Status closeDB(DB*& db);

  /**
   * @brief Put a key-value pair.
   *
   * @param key
   * @param value
   * @return Status
   */
  Status put(std::string const& key, std::string const& value);

  /**
   * @brief Delete a key.
   *
   * @param key
   * @return Status
   */
  Status remove(std::string const& key);

  /**
   * @brief Get the value of a key.
   *
   * value aliases bytes owned by guard. Keep guard alive while using value.
   *
   * @param key
   * @param value  output: pointer to the value bytes
   * @param guard  output: shared_ptr that keeps the value alive
   * @return Status
   */
  Status get(std::string const& key, std::string const*& value,
             std::shared_ptr<Record>& guard);

  /**
   * @brief Batch fence for strict reads (Metadata::linearizable_reads).
   *
   * sync() takes one storage fence on the calling thread. Every get() on this
   * thread reuses it until clearSync(), so each read linearizes at the sync()
   * point. Pair every sync() with a clearSync(): a leaked token pins later
   * reads on this thread to a stale fence. No-op on non-fencing backends and
   * in non-strict mode.
   */
  Status sync();
  Status clearSync();
};
}  // namespace ozonedb
#endif  // DB_H
