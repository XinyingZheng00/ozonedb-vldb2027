// src/include/ozonedb/disk_cache_storage.h
#pragma once

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <fstream>
#include <functional>
#include <list>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "frequency_sketch.h"
#include "storage.h"

namespace ozonedb {

// A read-through tier for immutable SSTable objects on a local disk, in
// front of the object store that holds them.
//
// Only names with `prefix` are cached. Every other name passes through to
// the backing store (checkpoint/LATEST is mutable and must never be cached).
//
// Chunk mode (default): one sparse local file per SSTable, one entry per
// entry_bytes chunk, CLOCK eviction with hole punching. See the private
// section.
//
// File mode: entries are whole files. "<dir>/<name>" is a complete local
// copy, "<dir>/<name>.part" a write-through in progress, and
// "<dir>/<name>.fillpart" a background fill in progress. The fill and the
// write-through never share a stream or a temp file. Population: the
// builder's write-through (append* + flush), a background fill on the first
// ranged-read miss, and reconcile() at open. Eviction: LRU by file under
// capacity_bytes. Invalidation: remove() (this process compacted) and
// invalidate() (a peer compacted, wired to the COMPACT apply in
// MetadataLogHandler).
//
// A fill in flight may publish a file a peer's COMPACT has removed from the
// View. SSTable content is immutable and a name is never reused, so the
// copy is dead, not wrong. The LRU or the next reconcile() drops it.
//
// Lock order and what each mutex covers:
//   * mtx_        - index_, lru_, current_bytes_ and, in chunk mode,
//                   chunks_, ring_, the CLOCK hand and present_chunks_.
//                   Held across an unlink in eviction and erase, and across
//                   punchHole(); never across pwriteLocal(), a backing-store
//                   call or other file I/O.
//   * parts_mtx_  - parts_ (the write-through streams) and poisoned_parts_.
//                   Held across the part's create, open, write, flush and
//                   remove. Never across a backing-store call, and never
//                   while mtx_ is held.
//   * fill_mtx_   - fill_queue_, queued_, the fill_* flags and fill_cv_.
//                   Never held across the fill's I/O: fillOne() runs
//                   unlocked.
//   * fill_lifecycle_mtx_ - serialises startFillWorker/stopFillWorker; held
//                   across fill_thread_.join(). The outermost of the four.
//   * sketch_mtx_ - sketch_ (the TinyLFU counters). A leaf: taken under mtx_
//                   by mayTakeLocked(), and alone by recordAccess(). Never
//                   held across I/O.
// Order: fill_lifecycle_mtx_ -> fill_mtx_. parts_mtx_ and mtx_ are never
// nested, and neither is taken under fill_mtx_. mtx_ -> sketch_mtx_.
// Nothing here takes a cache or View mutex; the DB calls the tier only
// outside view_mutex.
//
// The constructor throws std::runtime_error when `dir` cannot be created or
// is not writable.
class DiskCacheStorage : public Storage {
 public:
  enum class Admission { kAlways, kFrequency };
  enum class Mode { kFile, kChunk };

  struct Options {
    std::string dir;                 // local directory, "/" appended if missing
    uint64_t capacity_bytes = 0;     // 0 = cache nothing (pure pass-through)
    std::string prefix = "sstable";  // names starting with this are cached
    size_t chunk_bytes = 64u << 20;  // file mode only: ranged-read size of the fill worker
    bool drop_pages = true;          // POSIX_FADV_DONTNEED after each tier read
    size_t max_queue = 256;          // fill queue bound; the oldest is dropped
    Admission admission = Admission::kFrequency;  // kAlways: evict-to-fit
    uint64_t admit_window = 0;                    // sketch aging window in samples; 0 = 8 x the entries the budget holds
    Mode mode = Mode::kChunk;                     // kFile: whole-SSTable entries with a fill worker
    size_t entry_bytes = 64u << 10;               // chunk mode: entry size, a power of two >= 4096
  };

  struct Stats {
    uint64_t hits = 0, misses = 0, hit_bytes = 0, miss_bytes = 0, passthrough = 0;
    uint64_t fills = 0, fill_bytes = 0, fill_gets = 0;
    uint64_t fill_skipped_budget = 0, fill_skipped_present = 0, fill_gone = 0, fill_failed = 0, fill_dropped = 0;
    uint64_t writethrough_files = 0, evictions = 0, evicted_bytes = 0, invalidated = 0;
    uint64_t files = 0, bytes = 0, capacity = 0;
    uint64_t fetch_bytes = 0, admit_rejected = 0;
    uint64_t entries = 0, punch_failed = 0;
  };

  // Throws std::runtime_error when options.dir cannot be created or is not
  // writable (probed with a create + unlink).
  DiskCacheStorage(std::unique_ptr<Storage> backing, Options options);
  ~DiskCacheStorage() override;

  // Storage. Every method forwards to the backing store; the cacheable ones
  // also maintain the local copy.
  void createDirectory(std::string name) override;
  Status append(std::string const& fileName, unsigned char* const& data, int length) override;
  Status appendNoFlush(std::string const& fileName, unsigned char* const& data, int length) override;
  Status appendInBatch(std::string const& fileName, unsigned char* const& data, int length) override;
  Status flush(std::string const& fileName) override;
  Status read(std::string const& fileName, unsigned char*& data, size_t& size) override;
  Status read(std::string const& fileName, unsigned char*& data, size_t a, size_t length) override;
  size_t size(std::string fileName) override;
  void seal(std::string fileName) override;
  bool isSealed(std::string fileName) override;
  void remove(std::string fileName) override;
  bool exist(std::string fileName) override;
  void setRemoteAppendListener(RemoteAppendListener listener) override;
  long lastAppendAddressForThread() const override;
  void sync() override;
  void clearSync() override;
  bool hasSyncToken() const override;

  // Tier API.
  bool cacheable(std::string const& name) const;
  // Drops the local copy (complete or in progress); the object stays.
  void invalidate(std::string const& name);
  // Scans dir. Deletes .part and .fillpart files, and complete files for
  // which live(name, bytes) is false. Admits the rest oldest first. Returns
  // the number deleted.
  size_t reconcile(std::function<bool(std::string const&, size_t)> const& live);
  void startFillWorker();
  void stopFillWorker();
  void waitFillIdle();
  Stats stats();
  void printStats();
  Storage* backing() { return backing_.get(); }
  Options const& options() const { return options_; }

 private:
  struct Entry {
    size_t bytes = 0;
    std::list<std::string>::iterator lru;
  };

  // How a candidate takes budget that is not free.
  //   kForce    evict until it fits: reconcile(), and every path under
  //             Admission::kAlways.
  //   kContest  only if the candidate's sketch frequency is strictly above
  //             the victim's: fills under Admission::kFrequency.
  //   kFreeOnly never: write-throughs under Admission::kFrequency, because a
  //             compaction output has no access history.
  // Free budget is always taken without a contest. A refusal may be counted
  // twice when the budget changes between a fill's check and its publish.
  enum class AdmitMode { kForce, kContest, kFreeOnly };
  AdmitMode fillMode() const { return options_.admission == Admission::kFrequency ? AdmitMode::kContest : AdmitMode::kForce; }
  AdmitMode writeThroughMode() const { return options_.admission == Admission::kFrequency ? AdmitMode::kFreeOnly : AdmitMode::kForce; }
  void recordAccess(std::string const& key);
  uint32_t frequency(std::string const& key);  // 0 without a sketch
  // Under mtx_. Decides whether `bytes` more for `key` is allowed under `how`,
  // given `victim` (empty = none). Counts a refusal in admit_rejected_.
  bool mayTakeLocked(std::string const& key, size_t bytes, std::string const& victim, AdmitMode how);
  // backing_->size() memoised per cacheable name; objects are immutable.
  // Dropped by eraseLocked and invalidate. Fill path only: the public size()
  // reports the backing size for a file the builder is appending.
  size_t sizeOf(std::string const& name);

  // Chunk mode. One sparse local file per SSTable under localPath(name);
  // one state byte per chunk. A chunk is charged chunkBytes() of budget
  // while kPresent or kPending. kPending is a reservation: the bytes are on
  // their way. A reader must not treat the chunk as a hit (a hole reads as
  // zeros). The CLOCK hand must not evict it. Only the thread that set
  // kPending clears it.
  static constexpr uint8_t kPresent = 1, kReferenced = 2, kPending = 4;
  struct ChunkFile {
    size_t size = 0;             // object size
    std::vector<uint8_t> state;  // chunkCount(size) bytes
    size_t present = 0;          // chunks with kPresent
    size_t pending = 0;          // chunks with kPending
    size_t ring = 0;             // this file's slot in ring_
  };
  size_t chunkCount(size_t size) const { return (size + options_.entry_bytes - 1) / options_.entry_bytes; }
  size_t chunkBytes(size_t size, size_t c) const { return std::min(options_.entry_bytes, size - c * options_.entry_bytes); }
  std::string chunkKey(std::string const& name, size_t c) const { return name + '#' + std::to_string(c); }
  Status readChunked(std::string const& fileName, unsigned char*& data, size_t a, size_t length);
  // Under mtx_. True when every chunk of [c0, c1) is kPresent; sets kReferenced on them.
  bool chunksPresentLocked(ChunkFile& cf, size_t c0, size_t c1);
  // Under mtx_. Marks chunk c kPending and charges its bytes, evicting through the
  // CLOCK hand as needed under `how`. False (nothing changed) when refused.
  bool reserveChunkLocked(std::string const& name, ChunkFile& cf, size_t c, AdmitMode how);
  // Under mtx_. Advances the hand to the next kPresent chunk without kReferenced,
  // clearing kReferenced on the way. False after two idle rotations.
  bool clockVictimLocked(std::string& name, size_t& chunk);
  void evictChunkLocked(std::string const& name, size_t chunk);
  // Under mtx_. Forgets the file's accounting and its ring slot. `unlink`
  // also removes the local file.
  void dropChunkFileLocked(std::string const& name, bool unlink, bool count_as_invalidated);
  // Under mtx_, chunk mode. Indexes localPath(name) as a complete file of
  // `bytes` (every chunk kPresent | kReferenced). Takes budget under `how`
  // chunk by chunk through the CLOCK hand. False (nothing indexed) when refused.
  bool admitWholeFileLocked(std::string const& name, size_t bytes, AdmitMode how);
  bool pwriteLocal(std::string const& name, size_t off, unsigned char const* buf, size_t len);
  bool punchHole(std::string const& name, size_t off, size_t len);

  std::string localPath(std::string const& name) const { return options_.dir + name; }
  std::string partPath(std::string const& name) const { return options_.dir + name + ".part"; }
  // The fill's own temp file, never shared with parts_, writePart or
  // discardPart.
  std::string fillPartPath(std::string const& name) const { return options_.dir + name + ".fillpart"; }
  // Returns true and moves the entry to the LRU front when a complete copy exists.
  bool touch(std::string const& name);
  bool present(std::string const& name);
  // pread of [a, a+length) from the local copy into a new buffer; false on any short read.
  bool readLocal(std::string const& name, unsigned char*& data, size_t a, size_t length);
  // Appends to the .part stream (opened on first use). A write failure
  // poisons the name, and a poisoned name is skipped. A reopen with trunc
  // would publish the tail of the file as a whole one.
  void writePart(std::string const& name, unsigned char const* data, size_t length);
  // Closes the .part, renames it into place and admits it; discards it on
  // any failure. On a poisoned name it removes the part, clears the poison
  // and returns false: nothing is published for that name.
  bool publishPart(std::string const& name);
  // Drops a write-through in progress and poisons the name, so later
  // appends cannot be published as a complete copy.
  void discardPart(std::string const& name);
  // Shared publish tail for a finished file at `part_path`. Checks its size
  // against `expected_size` when nonzero, checks the budget, renames into
  // place and admits it. False, with the part removed, on any failure.
  bool publishPartFile(std::string const& name, std::string const& part_path, size_t expected_size, AdmitMode how);
  // Under mtx_: evicts until `bytes` fits, then inserts. False when bytes > capacity.
  bool admit(std::string const& name, size_t bytes, AdmitMode how);
  void evictToFitLocked(size_t bytes);
  void eraseLocked(std::string const& name, bool count_as_invalidated);
  void enqueueFill(std::string const& name);
  void fillLoop();
  void fillOne(std::string const& name);

  std::unique_ptr<Storage> backing_;
  Options options_;

  std::mutex mtx_;
  std::unordered_map<std::string, Entry> index_;
  std::list<std::string> lru_;  // front = most recently used
  uint64_t current_bytes_ = 0;
  std::unordered_map<std::string, size_t> sizes_;  // under mtx_
  std::unordered_map<std::string, ChunkFile> chunks_;  // chunk mode, under mtx_
  std::vector<std::string> ring_;                      // CLOCK order of the chunks_ keys
  size_t hand_file_ = 0, hand_chunk_ = 0;              // the CLOCK hand: ring_ slot, chunk
  uint64_t present_chunks_ = 0;                        // under mtx_

  // Leaf lock for sketch_: taken under mtx_ by mayTakeLocked(), and alone by
  // recordAccess(). Never held across any I/O.
  std::mutex sketch_mtx_;
  std::unique_ptr<FrequencySketch> sketch_;

  std::mutex parts_mtx_;
  std::unordered_map<std::string, std::unique_ptr<std::ofstream>> parts_;
  std::unordered_set<std::string> poisoned_parts_;

  std::mutex fill_mtx_;
  // Serialises startFillWorker/stopFillWorker across the join. Two stops
  // cannot both join the same thread, and a start cannot race a stop's join.
  std::mutex fill_lifecycle_mtx_;
  std::condition_variable fill_cv_;
  std::deque<std::string> fill_queue_;
  std::unordered_set<std::string> queued_;
  bool fill_started_ = false;
  bool fill_stop_ = false;
  bool fill_stopping_ = false;  // set for the duration of a stopFillWorker() call
  bool fill_busy_ = false;
  std::thread fill_thread_;

  std::atomic<uint64_t> hits_{0}, misses_{0}, hit_bytes_{0}, miss_bytes_{0}, passthrough_{0};
  std::atomic<uint64_t> fills_{0}, fill_bytes_{0}, fill_gets_{0};
  std::atomic<uint64_t> fill_skipped_budget_{0}, fill_skipped_present_{0}, fill_gone_{0}, fill_failed_{0}, fill_dropped_{0};
  std::atomic<uint64_t> writethrough_files_{0}, evictions_{0}, evicted_bytes_{0}, invalidated_{0};
  std::atomic<uint64_t> fetch_bytes_{0}, admit_rejected_{0}, punch_failed_{0};
};

}  // namespace ozonedb
