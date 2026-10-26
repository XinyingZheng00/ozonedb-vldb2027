#ifndef OZONEDB_LOG_KEY_INDEX_H
#define OZONEDB_LOG_KEY_INDEX_H

#include "protobuf/record.pb.h"
#include <cstddef>
#include <cstdint>
#include <list>
#include <memory>
#include <shared_mutex>
#include <string>
#include <unordered_map>

namespace ozonedb {

// LogKeyIndex (below) is the per-LogHandler in-memory index from key to
// newest Record. readRecord uses it to skip the file scan and, on Corfu,
// the fence. Records are co-owned with the LRUCache through shared_ptr,
// so a cache invalidate cannot free a Record a reader holds.
//
// LogRank is the position of a record in the shared log's global order.
// addr is the Corfu global address of the entry that carried the record.
// sub orders records inside one entry: a later record is newer.
// A source with no global order (local backend, backfill scan) uses
// addr = -1. It never displaces a ranked entry and any ranked entry
// displaces it.
// At namespace scope: a nested type's default member initializers cannot
// be used in a default argument of the enclosing, still incomplete class.
struct LogRank {
  long addr = -1;
  uint32_t sub = 0;

  bool operator<(LogRank const& o) const {
    if (addr != o.addr) return addr < o.addr;
    return sub < o.sub;
  }
};

class LogKeyIndex {
 public:
  using Rank = LogRank;

  explicit LogKeyIndex(size_t capacity) : capacity_(capacity) {}

  std::shared_ptr<Record> lookup(std::string const& key);
  // Insert or replace the entry for key. Newest rank wins: a record whose
  // rank is below the stored one is dropped, because the tailer can
  // deliver a peer's older record after a newer local one.
  void upsert(std::string const& key, std::shared_ptr<Record> rec,
              std::string const& source_file, LogRank rank = LogRank{});
  void invalidateFile(std::string const& file);
  size_t size() const;

 private:
  struct Entry {
    std::shared_ptr<Record> rec;
    std::string source_file;
    LogRank rank;
    std::list<std::string>::iterator lru_it;
  };

  void evictOneLocked();

  mutable std::shared_mutex mutex_;
  std::unordered_map<std::string, Entry> map_;
  std::list<std::string> lru_;
  size_t capacity_;
};

}  // namespace ozonedb
#endif
