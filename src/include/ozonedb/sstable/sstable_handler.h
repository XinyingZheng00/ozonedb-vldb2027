#ifndef SSTABLE_HANDLER_H
#define SSTABLE_HANDLER_H
#include "cache.h"
#include "log_handler.h"
#include "storage.h"
#include "thread_pool.h"

namespace ozonedb {
// Reads a key from the SSTable levels of the latest View.
// SSTable names carry the writer's fingerprint, so writers never collide.
// Every record in an SSTable has type kTypeValue.
class SSTableHandler {
 private:
  Storage* storage = nullptr;
  MetadataLogHandler* metadata_log = nullptr;
  std::string sstable_prefix;
  LRUCache* lru_cache = nullptr;
  ThreadPool* thread_pool = nullptr;
  int max_level;
  View const* latest_view = nullptr;

 public:
  /**
   * @brief Construct a handler over storage, the metadata log and the cache.
   */
  SSTableHandler(Storage* storage, MetadataLogHandler* metadata_log, std::string sstable_prefix, LRUCache* cache)
      : storage(storage), metadata_log(metadata_log), sstable_prefix(sstable_prefix), lru_cache(cache){};

  // view aliases the caller's View snapshot; the caller keeps it alive.
  void setLatestView(View const* view) { latest_view = view; }

  void setMaxLevel(int max_level) { this->max_level = max_level; }
  ~SSTableHandler(){};

  Status readRecordFromAllLevel(std::string const& key, std::shared_ptr<Record>& record, std::string const& offset);

  void setThreadPool(ThreadPool* thread_pool) { this->thread_pool = thread_pool; }
};
}  // namespace ozonedb
#endif