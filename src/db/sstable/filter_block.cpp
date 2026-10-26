// Copyright (c) 2012 The LevelDB Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file. See the AUTHORS file for names of contributors.

#include "sstable/filter_block.h"
#include "protobuf_serializer.h"
#include "sstable/filter_policy.h"
#include <cassert>

namespace ozonedb {

// One filter per kFilterBase (2 KB) of data block offset.
/*
Blocks smaller than kFilterBase share one filter.
A block larger than kFilterBase gets its own filter; the ranges it spans get empty filters.
*/
static const size_t kFilterBaseLg = 11;
static const size_t kFilterBase = 1 << kFilterBaseLg;

FilterBlockBuilder::FilterBlockBuilder(FilterPolicy const* policy)
    : policy_(policy) {}

void FilterBlockBuilder::startBlock(uint64_t block_offset) {
  uint64_t filter_index = (block_offset / kFilterBase);
  assert(filter_index >= block_.filters_size());
  while (filter_index > block_.filters_size()) {
    generateFilter();
  }
}

void FilterBlockBuilder::addKey(std::string const& key) {
  tmp_keys_.push_back(key);
}

FilterBlock& FilterBlockBuilder::finish() {
  if (!tmp_keys_.empty()) {
    generateFilter();
  }
  block_.set_lg_base(kFilterBaseLg);  // Save encoding parameter in result
  return block_;
}

void FilterBlockBuilder::generateFilter() {
  const size_t num_keys = tmp_keys_.size();
  if (num_keys == 0) {
    // Fast path if there are no keys for this filter
    block_.add_filters("");
    return;
  }

  // Build a filter over the pending keys and append it.
  std::string filter;
  policy_->createFilter(tmp_keys_.data(), static_cast<int>(num_keys), &filter);
  block_.add_filters(filter);

  tmp_keys_.clear();
}

filterBlockReader::filterBlockReader(FilterPolicy const* policy, FilterBlock* block)
    : policy_(policy), block_(block) {
}

bool filterBlockReader::keyMayMatch(uint64_t block_offset, std::string const& key) {
  uint64_t index = block_offset >> block_->lg_base();
  if (index < block_->filters_size()) {
    // Borrow the filter bytes; never copy them. The file-level filter
    // covers every key of the file and is probed on every get.
    std::string const& filter = block_->filters(index);
    return policy_->keyMayMatch(key, filter);
  }
  return true;  // Errors are treated as potential matches
}

Status readFilterBlock(Storage* storage,
                       std::string const& fileName,
                       BlockIdentifier const& identifier,
                       FilterBlock*& result) {
  // Read and parse the filter block; TableBuilder::finish wrote it.
  size_t n = identifier.length();
  unsigned char* block_data_contents = nullptr;
  Status status = storage->read(fileName, block_data_contents, identifier.offset(), n);
  if (status != Status::kSuccess) {
    return status;
  }
  std::vector<google::protobuf::Message*> block_data;
  status = protobuf::deserializeMessages(block_data_contents, n, block_data, []() {
    return new FilterBlock();
  });
  delete[] block_data_contents;
  block_data_contents = nullptr;
  // A failed or empty parse is an error; the caller treats the file as
  // having no filter.
  if (status != Status::kSuccess || block_data.empty()) {
    for (auto* m : block_data) delete m;
    result = nullptr;
    return status == Status::kSuccess ? Status::kFailure : status;
  }
  result = static_cast<FilterBlock*>(block_data[0]);
  for (size_t i = 1; i < block_data.size(); ++i) delete block_data[i];
  return status;
}

}  // namespace ozonedb
