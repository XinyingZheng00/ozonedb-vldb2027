// Copyright (c) 2011 The LevelDB Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file. See the AUTHORS file for names of contributors.

#ifndef STORAGE_OZONEDB_INCLUDE_COMPARATOR_H_
#define STORAGE_OZONEDB_INCLUDE_COMPARATOR_H_

#include <string>
#include <stdint.h>

namespace ozonedb {

// A Comparator gives a total order over the strings used as keys.
// Implementations must be thread-safe: methods run concurrently on many threads.
class Comparator {
 public:
  virtual ~Comparator();

  // Three-way comparison.  Returns value:
  //   < 0 iff "a" < "b",
  //   == 0 iff "a" == "b",
  //   > 0 iff "a" > "b"
  virtual int compare(std::string const& a, std::string const& b) const = 0;

  // Name of the comparator; it detects a DB opened with a different one.
  // Change the name when the key order changes.
  // Names that start with "leveldb." are reserved.
  virtual char const* name() const = 0;

  // The next two methods shorten the keys stored in index blocks.

  // If *start < limit, change *start to a short string in [start, limit).
  // Leaving *start unchanged is correct.
  virtual void findShortestSeparator(
      std::string* start,
      std::string const& limit) const = 0;

  // Change *key to a short string >= *key.
  // Leaving *key unchanged is correct.
  virtual void findShortSuccessor(std::string* key) const = 0;
};

// Return a new comparator with lexicographic byte-wise order.
extern Comparator* newBytewiseComparator();

extern void deleteBytewiseComparator();
}  // namespace ozonedb
#endif  // STORAGE_OZONEDB_INCLUDE_COMPARATOR_H_
