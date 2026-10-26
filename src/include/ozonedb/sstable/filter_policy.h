// Copyright (c) 2012 The LevelDB Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file. See the AUTHORS file for names of contributors.
//
// A FilterPolicy builds a small filter from a set of keys.
// The table reader consults the filter before it reads a block.
// Most users want the bloom filter from newBloomFilterPolicy().

#ifndef STORAGE_OZONEDB_INCLUDE_FILTER_POLICY_H_
#define STORAGE_OZONEDB_INCLUDE_FILTER_POLICY_H_

#include <cstdint>
#include <string>
#ifndef FALLTHROUGH_INTENDED
#define FALLTHROUGH_INTENDED \
  do {                       \
  } while (0)
#endif
namespace ozonedb {

inline uint32_t DecodeFixed32(char const* ptr) {
  auto const* const buffer = reinterpret_cast<uint8_t const*>(ptr);

  return (static_cast<uint32_t>(buffer[0])) |
         (static_cast<uint32_t>(buffer[1]) << 8) |
         (static_cast<uint32_t>(buffer[2]) << 16) |
         (static_cast<uint32_t>(buffer[3]) << 24);
}

inline uint32_t Hash(char const* data, size_t n, uint32_t seed) {
  // Similar to murmur hash
  uint32_t const m = 0xc6a4a793;
  uint32_t const r = 24;
  char const* limit = data + n;
  uint32_t h = seed ^ (n * m);

  // Pick up four bytes at a time
  while (data + 4 <= limit) {
    uint32_t w = DecodeFixed32(data);
    data += 4;
    h += w;
    h *= m;
    h ^= (h >> 16);
  }

  // Pick up remaining bytes
  switch (limit - data) {
    case 3:
      h += static_cast<uint8_t>(data[2]) << 16;
      FALLTHROUGH_INTENDED;
    case 2:
      h += static_cast<uint8_t>(data[1]) << 8;
      FALLTHROUGH_INTENDED;
    case 1:
      h += static_cast<uint8_t>(data[0]);
      h *= m;
      h ^= (h >> r);
      break;
  }
  return h;
}

class FilterPolicy {
 public:
  virtual ~FilterPolicy(){};

  // Name of this policy. Change it when the filter encoding changes,
  // so old filters are not fed to new code.
  virtual char const* name() const = 0;

  // Append a filter over keys[0, n-1] to *dst. The keys are sorted and may repeat.
  // Do not modify the existing contents of *dst.
  virtual void createFilter(std::string const* keys, int n,
                            std::string* dst) const = 0;

  // filter holds the bytes one createFilter() call appended.
  // Must return true if key was in that call's key list.
  // May return true for other keys, but should return false with high probability.
  virtual bool keyMayMatch(std::string const& key, std::string const& filter) const = 0;
};

// Return a bloom filter policy with about bits_per_key bits per key.
// 10 gives about 1% false positives.
// The caller deletes the result after every DB that uses it is closed.
// A comparator that ignores part of a key needs a policy that ignores the same part.
FilterPolicy const* newBloomFilterPolicy(int bits_per_key);

}  // namespace ozonedb

#endif  // STORAGE_OZONEDB_INCLUDE_FILTER_POLICY_H_
