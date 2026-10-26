// Copyright (c) 2011 The LevelDB Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file. See the AUTHORS file for names of contributors.

#ifndef STORAGE_OZONEDB_TABLE_BLOCK_BUILDER_H_
#define STORAGE_OZONEDB_TABLE_BLOCK_BUILDER_H_

#include "comparator.h"
#include "protobuf/sstable.pb.h"
#include "storage.h"
#include <string>
#include <vector>
#include <stdint.h>
namespace ozonedb {

class BlockBuilder {
 public:
  explicit BlockBuilder(int block_restart_interval);

  // Reset to the state after construction.
  void reset();

  // REQUIRES: finish() not called since the last reset().
  // REQUIRES: key is larger than every key added before.
  void add(std::string const& key, std::string const& value);

  // Finish the block and return it. The returned BlockData stays valid
  // until reset() or the destruction of this builder.
  BlockData& finish();

  // Estimated size of the block under construction.
  size_t currentSizeEstimate() const;

  // True iff no entry was added since the last reset().
  bool empty() const {
    return block_.entries_size() == 0;
  }

 private:
  Comparator* comparator_;
  int block_restart_interval;       // Restart interval
  BlockData block_;                 // Destination block
  int accumulated_size_;            // Serialized size of the entries added so far
  std::vector<uint32_t> restarts_;  // Restart points
  int counter_;                     // Entries emitted since the last restart
  bool finished_;

 public:
  std::string last_key_;
};

Status readBlock(Storage* storage,
                 std::string const& fileName,
                 BlockIdentifier const& identifier,
                 BlockData*& result);
}  // namespace ozonedb
#endif  // STORAGE_OZONEDB_TABLE_BLOCK_BUILDER_H_
