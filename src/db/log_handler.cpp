#include "log_handler.h"
#include "db.h"
#include "helper.h"
#include <google/protobuf/message.h>
#include <future>
#include <iostream>
#include <mutex>
#include <regex>
#include <unordered_map>
#include <vector>

namespace ozonedb {
Status LogHandler::newTail() {
  if (!this->active_unit.empty()) {
    this->storage->seal(this->active_unit);
  }
  View view;
  metadata_log->getLatestView(view);
  std::string current_tail = view.current_log_tail;
  // The tail name has the format prefix/number.
  int log_number;
  if (current_tail.empty()) {
    log_number = 0;
  } else {
    if (view.getFileSize(current_tail) < this->file_size_limit) {
      // The current tail still has room: keep it.
      this->active_unit = current_tail;
      return Status::kSuccess;
    }
    log_number = std::stoi(getSuffix(current_tail));
  }
  std::string name = this->prefix + "/" + std::to_string(log_number + 1);
  OperationRecord record;
  record.set_op_type(OperationRecord::LOGCREATE);
  record.add_input_files(current_tail);
  record.add_output_file(name);
  // Ship the sealed tail's size so a peer rollforward can fill file_size
  // without a storage->size() call under view_mutex. This is the writer's
  // unfenced view size: use it only for compaction sizing, never as a read
  // bound. Readers use a fenced storage->size() (LRUCache::sealedLogSize).
  if (!current_tail.empty()) {
    record.set_sealed_input_bytes(
        static_cast<int64_t>(view.getFileSize(current_tail)));
  }
  this->metadata_log->appendToMetadataLog(record);
  view = this->metadata_log->rollForwardMetadataLog();
  this->active_unit = view.current_log_tail;
  if (this->metadata_log->event_listener != nullptr) {
    this->metadata_log->event_listener->onNewTail();
  }
  return Status::kSuccess;
}

Status LogHandler::addRecord(Record const& record) {
  int buffer_size;
  unsigned char* buffer = protobuf::serializeMessage(record, buffer_size);
  if (this->active_unit.empty()) {
    newTail();
  }

  // One append, one effect point. kSealed is the only retryable status:
  // it means no bytes landed, so a retry on the new tail cannot duplicate
  // the record. The roll happens before the append, never after a success.
  // A record on a tail a peer seals stays readable, because a sealed log
  // is read to its fenced size (LRUCache::checkReadMoreLog). Any status
  // other than kSuccess is not acked.
  constexpr int kMaxRetries = 8;
  std::string final_target;
  std::string last_target;
  int attempts = 0;
  Status status = Status::kFailure;
  for (int attempt = 0; attempt <= kMaxRetries; ++attempt) {
    ++attempts;
    // Roll before the append when the tail is full or a peer moved it.
    // newTail() re-checks under its own view. Read the snapshot lock-free;
    // do not deep-copy the View.
    if (this->active_unit.empty()) {
      newTail();
    } else {
      auto snap = metadata_log->latestViewSnapshot();
      if (!snap || snap->current_log_tail != this->active_unit ||
          snap->getFileSize(this->active_unit) >= this->file_size_limit) {
        newTail();
      }
    }
    std::string target = this->active_unit;
    if (target.empty()) break;
    last_target = target;

    status = this->storage->appendInBatch(target, buffer, buffer_size);
    if (status == Status::kSuccess) {
      final_target = target;
      break;
    }
    if (status != Status::kSealed) break;  // Hard failure: do not retry.

    // kSealed: a peer or our own roll closed this tail. Roll the View
    // forward before newTail(), or a stale View re-adopts the sealed file.
    metadata_log->rollForwardMetadataLog();
    newTail();
  }

  delete[] buffer;
  buffer = nullptr;

  // Never cache or index a record the log did not take.
  if (status != Status::kSuccess || final_target.empty()) {
    std::cerr << "[log] addRecord: append not taken (status="
              << static_cast<int>(status) << " target=" << last_target
              << " attempts=" << attempts << ")\n";
    return Status::kFailure;
  }

  // Put the record in the cache and the index. Cache and index co-own it
  // through shared_ptr, so a reader's copy outlives an invalidation.
  if (key_index_) {
    auto clone = std::make_shared<Record>(record);
    cache->putLogRecordSingle(final_target, clone);
    // Rank by the global address the log assigned. A peer record the
    // tailer delivers later displaces it only when it is newer. Backends
    // with no global order return -1.
    LogKeyIndex::Rank rank{this->storage->lastAppendAddressForThread(), 0};
    key_index_->upsert(record.key(), std::move(clone), final_target, rank);
  }
  return Status::kSuccess;
}

bool LogHandler::tryIndexLookup(std::string const& key, std::shared_ptr<Record>& record) {
  if (!key_index_) return false;
  auto hit = key_index_->lookup(key);
  if (!hit) return false;
  record = std::move(hit);
  return true;
}

Status LogHandler::readRecord(std::string const& key, std::shared_ptr<Record>& record, std::string const& offset, std::string& latest_offset) {
  // Fast path: probe the key index before the file scan. A miss falls
  // through to the scan, which backfills the index on a hit. Strict mode
  // skips the probe (see linearizable_reads_).
  if (key_index_ && !linearizable_reads_) {
    if (auto hit = key_index_->lookup(key)) {
      record = std::move(hit);
      return Status::kSuccess;
    }
    // With trust_background_tail the index is authoritative: a miss is a
    // log miss and the DB falls through to the SSTables.
    if (trust_background_tail_) {
      return Status::kFailure;
    }
  }
  auto const& files = this->latest_view->getWithPrefix(this->prefix);
  if (files.empty()) {
    return Status::kFailure;
  }
  std::shared_mutex record_mutex;
  int finished_threads = 0;
  int record_file = -1;
  std::mutex cv_mutex;
  std::condition_variable cv;

  int count = 0;
  for (int i = files.size() - 1; i >= 0; i--) {
    std::string const& file_name = files[i];
    bool read_more = true;  // Not cached, or the file is the unsealed tail.
    size_t cached_offset = 0;
    size_t size = 0;
    this->cache->checkReadMoreLog(file_name, read_more, cached_offset, size);
    if (read_more) {
      if (linearizable_reads_) {
        // Strict mode reads inline on the caller thread: the fence token
        // is thread-local, and a pool thread would fence again. A hit ends
        // the newest-first scan.
        this->cache->readDataLog(file_name, cached_offset, size);
        std::shared_ptr<Record> record_tmp;
        cache->getLog(file_name, key, record_tmp);
        if (record_tmp) {
          std::unique_lock<std::shared_mutex> lock(record_mutex);
          if (i >= record_file) {
            record = std::move(record_tmp);
            record_file = i;
          }
          break;
        }
      } else {
        count++;
        thread_pool->enqueue([this, file_name, cached_offset, size, key, i,
                              &record_file, &record, &record_mutex, &cv, &cv_mutex, &finished_threads]() {
          this->cache->readDataLog(file_name, cached_offset, size);
          std::shared_ptr<Record> record_tmp;
          cache->getLog(file_name, key, record_tmp);
          if (record_tmp) {
            std::unique_lock<std::shared_mutex> lock(record_mutex);
            if (i >= record_file) {
              record = std::move(record_tmp);
              record_file = i;
            }
          }
          {
            std::lock_guard<std::mutex> lock(cv_mutex);
            finished_threads++;
          }
          cv.notify_one();
        },
                             ThreadPool::Priority::High);
      }
    } else {
      std::shared_ptr<Record> record_tmp;
      cache->getLog(file_name, key, record_tmp);
      if (record_tmp) {
        std::unique_lock<std::shared_mutex> lock(record_mutex);
        if (i >= record_file) {
          record = std::move(record_tmp);
          record_file = i;
        }
        break;
      }
    }
    if (offset == file_name) {
      break;
    }
  }

  // Wait for all pool threads.
  {
    std::unique_lock<std::mutex> lock(cv_mutex);
    cv.wait(lock, [&] { return finished_threads == count; });
  }
  // Report the newest file as latest_offset.
  if (*files.rbegin() != offset) {
    latest_offset = *files.rbegin();
  }

  if (record) {
    // Backfill the index from the scan hit. Unranked (addr = -1): the hit
    // fills an empty or unranked slot but never displaces a ranked entry.
    if (key_index_ && record_file >= 0 &&
        record_file < static_cast<int>(files.size())) {
      key_index_->upsert(key, record, files[record_file]);
    }
    return Status::kSuccess;
  }
  return Status::kFailure;
}

Status LogHandler::warmKeyIndex() {
  if (!key_index_ || latest_view == nullptr) return Status::kSuccess;
  auto const& files = this->latest_view->getWithPrefix(this->prefix);
  for (auto const& file_name : files) {
    bool read_more = true;
    size_t cached_offset = 0;
    size_t size = 0;
    this->cache->checkReadMoreLog(file_name, read_more, cached_offset, size);
    if (read_more) {
      this->cache->readDataLog(file_name, cached_offset, size);
    }
    std::unordered_map<std::string, std::shared_ptr<Record>> snapshot;
    this->cache->snapshotLogFileRecords(file_name, snapshot);
    // Files go oldest to newest, so the last write wins, as in readRecord.
    for (auto const& kv : snapshot) {
      key_index_->upsert(kv.first, kv.second, file_name);
    }
  }
  return Status::kSuccess;
}

void LogHandler::invalidateCompactedLog(std::vector<std::string> const& files) {
  for (auto const& f : files) {
    if (key_index_) key_index_->invalidateFile(f);
    if (cache) cache->invalidateLogFile(f);
  }
}

void LogHandler::onRemoteAppend(std::string const& file_name,
                                unsigned char const* data, size_t len,
                                RemoteOp op, long addr) {
  if (!key_index_) return;
  // Ignore files outside the data log prefix before the parse.
  if (file_name.compare(0, prefix.size(), prefix) != 0) return;

  if (op == RemoteOp::kRemove) {
    key_index_->invalidateFile(file_name);
    if (cache) cache->invalidateLogFile(file_name);
    return;
  }

  // APPEND payload: varint-prefixed Record protos, the format addRecord
  // writes with protobuf::serializeMessage.
  std::vector<google::protobuf::Message*> messages;
  protobuf::deserializeMessages(const_cast<unsigned char*>(data), len, messages,
                                []() -> google::protobuf::Message* {
                                  return new Record();
                                });
  // Records in one entry share its global address. The position in the
  // payload breaks the tie: a later record is newer.
  uint32_t sub = 0;
  for (auto* msg : messages) {
    // The shared_ptr owns the record from here, also if a put throws.
    std::shared_ptr<Record> rec(static_cast<Record*>(msg));
    auto key = rec->key();
    cache->putLogRecordSingle(file_name, rec);
    key_index_->upsert(std::move(key), std::move(rec), file_name,
                       LogKeyIndex::Rank{addr, sub++});
  }
}
}  // namespace ozonedb
