// Copyright (c) 2011 The LevelDB Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file. See the AUTHORS file for names of contributors.
//
// An Iterator yields key/value pairs from a source such as a block.
// Const methods may run on many threads without synchronization.
// Any non-const call requires external synchronization for the whole Iterator.

#ifndef STORAGE_OZONEDB_INCLUDE_ITERATOR_H_
#define STORAGE_OZONEDB_INCLUDE_ITERATOR_H_

#include "comparator.h"
#include "protobuf/sstable.pb.h"
#include "storage.h"
#include <iostream>
namespace ozonedb {

class Iterator {
 public:
  BlockData* block_data_ = nullptr;  // owned; the iterator deletes it
  Iterator() = default;
  virtual ~Iterator() = default;

  // True iff the iterator is positioned at an entry.
  virtual bool valid() const = 0;

  // Position at the first entry. valid() iff the source is not empty.
  virtual void seekToFirst() = 0;

  // Position at the last entry. valid() iff the source is not empty.
  virtual void seekToLast() = 0;

  // Position at the first entry at or past target. valid() iff one exists.
  virtual void seek(std::string const& target) = 0;

  // Move to the next entry. valid() iff the iterator was not at the last entry.
  // REQUIRES: valid()
  virtual void next() = 0;

  // Move to the previous entry. valid() iff the iterator was not at the first entry.
  // REQUIRES: valid()
  virtual void prev() = 0;

  // Key of the current entry.
  // REQUIRES: valid()
  virtual std::string key() const = 0;

  // Value of the current entry. The reference is valid until the next
  // change of the iterator.
  // REQUIRES: valid()
  virtual std::string const& value() const = 0;

  // Status of the last operation; kSuccess if no error occurred.
  virtual Status const& status() const = 0;
};

class BlockIter : public Iterator {
 private:
  Comparator* comparator_;
  google::protobuf::RepeatedPtrField<BlockEntry> const& entries_;  // aliases the BlockData entries
  google::protobuf::RepeatedField<uint32_t> const& restarts_;
  const uint32_t num_restarts_;  // Entries in restarts_

  uint32_t current_entry_index;  // Index in entries_ of the current entry; -1 if !valid()
  uint32_t restart_index_;       // Restart region of the current entry; -1 if !valid()
  std::string key_;
  std::string const* value_ptr_;
  Status status_;

  uint32_t nextEntryIndex() const;
  uint32_t getRestartPoint(uint32_t index);
  void seekToRestartPoint(uint32_t index);
  void corruptionError();
  bool parseCurrentKey();

 public:
  BlockIter(Comparator* comparator,
            google::protobuf::RepeatedPtrField<BlockEntry> const& entries_,
            google::protobuf::RepeatedField<uint32_t> const& restarts_,
            uint32_t num_restarts);
  ~BlockIter() { delete block_data_; };

  virtual bool valid() const override;
  virtual Status const& status() const override;
  virtual std::string key() const override;
  virtual std::string const& value() const override;
  virtual void next() override;
  virtual void prev() override;
  virtual void seek(std::string const& target) override;
  virtual void seekToFirst() override;
  virtual void seekToLast() override;
};

Iterator* newIterator(BlockData* block_data, ozonedb::Comparator* cmp);
}  // namespace ozonedb
#endif  // STORAGE_OZONEDB_INCLUDE_ITERATOR_H_
