// Copyright (c) 2011 The LevelDB Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file. See the AUTHORS file for names of contributors.

#ifndef STORAGE_OZONEDB_INCLUDE_TABLE_H_
#define STORAGE_OZONEDB_INCLUDE_TABLE_H_

#include "comparator.h"
#include "protobuf/sstable.pb.h"
#include "sstable/iterator.h"
#include "storage.h"
#include <functional>
#include <memory>
#include <stdint.h>
#include <unordered_map>
namespace ozonedb {
class LRUCache;
// A Table is an immutable sorted map from keys to records, stored in one file.
class Table {
 public:
  // Open the table in fileName: read the footer and the index block.
  // On success, set table; the caller deletes it. On failure, return the
  // error and leave table unchanged. storage must outlive the Table.
  static Status open(Storage* storage,
                     std::string const& fileName,
                     Table*& table);
  Status get(std::string const& key, std::shared_ptr<Record>& record);
  // Every record in the table, for compaction. The caller co-owns the
  // Records; the cache is not involved.
  // The data section is read in ranged reads of at most max_read_bytes.
  // No block is split across two reads; a block larger than the limit
  // gets its own read. The index is validated before the first read.
  // A bad index, a failed read, or a block or record that does not parse
  // returns kFailure or the storage status, and leaves out empty.
  static constexpr size_t kDefaultScanReadBytes = 64u << 20;
  Status getAll(std::unordered_map<std::string, std::shared_ptr<Record>>& out,
                size_t max_read_bytes = kDefaultScanReadBytes);

  // Publish every data block into cache with the same key bytes and size
  // as TableBuilder::flush. Reads the file in the same ranged chunks as
  // getAll. Blocks already in the cache stay as they are. Returns the
  // count of blocks and bytes published. On a failed read, the blocks
  // published so far stay in the cache. Safe on a shared Table: the scan
  // uses a private copy of the index block, never rep_->index_iter.
  Status warm(LRUCache* cache, size_t max_read_bytes, size_t& blocks, size_t& bytes);

  ~Table();

  void setCache(LRUCache* cache);
  Iterator* blockReader(Table* table, std::string const& index_value);
  // For tests: drop both filter readers.
  void setFilterReaderToNull();
  // For tests: override the file size that scanBlocks validates the index against.
  void setFileSizeForTesting(uint64_t size);

 private:
  struct Rep;
  Rep* rep_ = nullptr;

  explicit Table(Rep* rep) : rep_(rep) {}
  // Chunked scan shared by getAll and warm. Validates the index, reads
  // block-aligned chunks of at most max_read_bytes, and calls
  // visit(index_value, block_iter) once per data block in file order.
  // The scan owns the iterator. A visit that returns anything but
  // kSuccess stops the scan with that status.
  Status scanBlocks(size_t max_read_bytes,
                    std::function<Status(std::string const&, Iterator*)> const& visit);
  void readMeta(FooterBlock const& footer);
  void readFilter(std::string const& filter_handle_value);
  void readFileFilter(std::string const& filter_handle_value);
};
}  // namespace ozonedb
#endif  // STORAGE_OZONEDB_INCLUDE_TABLE_H_
