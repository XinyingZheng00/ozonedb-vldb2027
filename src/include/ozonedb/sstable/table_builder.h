// Copyright (c) 2011 The LevelDB Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file. See the AUTHORS file for names of contributors.
//
// TableBuilder builds a Table: an immutable sorted map from keys to values.
// Const methods may run on many threads without synchronization.
// Any non-const call requires external synchronization for the whole builder.

#ifndef STORAGE_OZONEDB_INCLUDE_TABLE_BUILDER_H_
#define STORAGE_OZONEDB_INCLUDE_TABLE_BUILDER_H_

#include "sstable/block_handler.h"
#include "storage.h"
#include <stdint.h>

namespace ozonedb {

class LRUCache;

class TableBuilder {
 public:
  // Create a builder that writes the table to file on storage.
  TableBuilder(Storage* storage, std::string file);

  // REQUIRES: finish() or abandon() has been called.
  ~TableBuilder();

  // Optional write-through cache. When set, flush() inserts the records
  // of every data block into the cache. Null skips the write-through.
  void setLRUCache(LRUCache* cache);

  // Add a record.
  // REQUIRES: key is greater than every key added before.
  // REQUIRES: finish() and abandon() not called.
  void add(std::string const& key, Record const& record);

  // Write the buffered data block to the file.
  // REQUIRES: finish() and abandon() not called.
  void flush();

  // kSuccess unless an error occurred.
  Status status() const;

  // Finish the table. The builder stops using the file after this returns.
  // REQUIRES: finish() and abandon() not called.
  Status finish();

  // Abandon the table. A caller that does not call finish() must call abandon().
  // REQUIRES: finish() and abandon() not called.
  void abandon();

  // Number of calls to add() so far.
  uint64_t numEntries() const;

  // Size of the file so far. After a successful finish(), the final file size.
  uint64_t fileSize() const;

  std::string fileName() const;

 private:
  struct Rep;
  Rep* rep_;

  bool ok() const { return status() == Status::kSuccess; }
  void writeBlock(google::protobuf::Message const& block, BlockIdentifier*& identifier);
};
}  // namespace ozonedb
#endif  // STORAGE_OZONEDB_INCLUDE_TABLE_BUILDER_H_
