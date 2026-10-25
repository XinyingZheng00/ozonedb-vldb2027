#ifndef LOG_HANDLER_H
#define LOG_HANDLER_H

#include "cache.h"
#include "log_key_index.h"
#include "metadata.h"
#include "metadata_log_handler.h"
#include "protobuf/record.pb.h"
#include "protobuf_serializer.h"
#include "storage.h"
#include "thread_pool.h"
#include <list>
#include <memory>
#include <string>
#include <vector>

namespace ozonedb {
class LogHandler {
 private:
  /**
   * @brief File name prefix of the data log, for example "<db_path>/datalog".
   */
  std::string prefix;
  std::string active_unit;
  View const* latest_view = nullptr;
  size_t file_size_limit;
  Storage* storage = nullptr;
  LRUCache* cache = nullptr;
  MetadataLogHandler* metadata_log = nullptr;
  ThreadPool* thread_pool = nullptr;

  /**
   * @brief Move to the next log file when the tail is full or sealed.
   *
   * @return Status
   */
  Status newTail();

 public:
  /**
   * @brief Construct a LogHandler.
   *
   * @param file_size_limit
   * @param log_prefix
   * @param storage
   */
  LogHandler(uint64_t file_size_limit, std::string log_prefix, Storage* storage,
             LRUCache* global_cache, MetadataLogHandler* metadata_log = nullptr,
             bool enable_key_index = false, size_t key_index_capacity = 1000000,
             bool trust_background_tail = false, bool linearizable_reads = false)
      : file_size_limit(file_size_limit), prefix(std::move(log_prefix)), storage(storage),
        trust_background_tail_(trust_background_tail),
        linearizable_reads_(linearizable_reads) {
    storage->createDirectory(prefix);
    cache = global_cache;
    this->metadata_log = metadata_log;
    if (enable_key_index) {
      key_index_ = std::make_unique<LogKeyIndex>(key_index_capacity);
      // Peer APPENDs and REMOVEs update the index through the storage's
      // remote-append listener. No-op on the local FS and S3 backends.
      storage->setRemoteAppendListener(
          [this](std::string const& file, unsigned char const* data,
                 size_t len, RemoteOp op, long addr) {
            this->onRemoteAppend(file, data, len, op, addr);
          });
    }
  };

  ~LogHandler() {
    // Clear the listener so the storage cannot call a destroyed object.
    // A callback in flight still holds the old copy, so ~DB must delete
    // log_handler before log_storage, whose destructor joins the tailer.
    if (storage != nullptr) {
      storage->setRemoteAppendListener(RemoteAppendListener{});
    }
  };

  // Set the View this handler reads. The pointer aliases the DB's snapshot.
  void setLatestView(View const* view) { latest_view = view; }

  /**
   * @brief Append a record to the data log.
   *
   * @param record
   * @return Status
   */
  Status addRecord(Record const& record);

  /**
   * @brief Read the newest record for key from the data log.
   *
   * @param key
   * @param record output
   * @return Status
   */
  Status readRecord(std::string const& key, std::shared_ptr<Record>& record, std::string const& offset, std::string& latest_offset);

  // Fast path: probe the key index without latest_view or storage.
  // Returns true and sets record on a hit. The result is authoritative
  // only when Metadata::trust_background_tail is set.
  bool tryIndexLookup(std::string const& key, std::shared_ptr<Record>& record);

  // Fill key_index_ from log files already in the cache. openDB calls it
  // once after the metadata rollforward.
  Status warmKeyIndex();

  // Drop key_index_ entries whose source_file is in files. Compaction
  // calls it after the COMPACT record and before it frees the merged
  // records. Idempotent.
  void invalidateCompactedLog(std::vector<std::string> const& files);

  // The storage calls this for a remote APPEND or REMOVE on the shared
  // log. It parses APPEND payloads into Records and puts them in the cache
  // and the index. Files outside this handler's prefix are ignored.
  // See Storage::setRemoteAppendListener.
  void onRemoteAppend(std::string const& file_name,
                      unsigned char const* data, size_t len, RemoteOp op, long addr);

  // Set the thread pool.
  void setThreadPool(ThreadPool* thread_pool) { this->thread_pool = thread_pool; }

 private:
  std::unique_ptr<LogKeyIndex> key_index_;
  bool trust_background_tail_ = false;
  // Strict mode (Metadata::linearizable_reads): readRecord does not serve
  // an unfenced index hit. The dispatch thread that feeds the index lags
  // the tailer, so a hit can predate a peer's acked put. The index is
  // still written so it stays warm.
  bool linearizable_reads_ = false;
};
}  // namespace ozonedb
#endif  // LOG_H