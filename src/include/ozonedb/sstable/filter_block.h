// Copyright (c) 2012 The LevelDB Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file. See the AUTHORS file for names of contributors.
//
// A filter block sits near the end of a table file.
// It holds the filters for every data block of the table.

#ifndef STORAGE_OZONEDB_TABLE_FILTER_BLOCK_H_
#define STORAGE_OZONEDB_TABLE_FILTER_BLOCK_H_

#include "protobuf/sstable.pb.h"
#include "storage.h"
#include <string>
#include <vector>

namespace ozonedb {

class FilterPolicy;

// Builds every filter of one table into one FilterBlock.
// Call sequence: (startBlock addKey*)* finish
class FilterBlockBuilder {
 public:
  explicit FilterBlockBuilder(FilterPolicy const*);
  void startBlock(uint64_t block_offset);
  void addKey(std::string const& key);
  FilterBlock& finish();

 private:
  void generateFilter();

  FilterPolicy const* policy_;
  std::vector<std::string> tmp_keys_;  // keys of the filter under construction
  FilterBlock block_;
};

class filterBlockReader {
 public:
  // Takes ownership of block. *policy must outlive *this.
  filterBlockReader(FilterPolicy const* policy, FilterBlock* block);
  bool keyMayMatch(uint64_t block_offset, std::string const& key);
  ~filterBlockReader() { delete block_; };

 private:
  FilterPolicy const* policy_;
  FilterBlock* block_;
};

Status readFilterBlock(Storage* storage, std::string const& fileName,
                       BlockIdentifier const& identifier,
                       FilterBlock*& result);

}  // namespace ozonedb

#endif  // STORAGE_OZONEDB_TABLE_FILTER_BLOCK_H_
