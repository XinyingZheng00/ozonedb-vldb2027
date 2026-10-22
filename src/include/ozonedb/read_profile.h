#ifndef OZONEDB_READ_PROFILE_H
#define OZONEDB_READ_PROFILE_H
// Read-path profile counters, compiled in with -DOZONEDB_PROFILE_READS=ON.
//
// Used for the Fig 1 investigation of where a get spends its time: the
// view snapshot, the log scan, and the SSTable probe, with per-level and
// per-table breakdowns of the probe. Off by default: the probes are two
// clock reads and a handful of relaxed atomics per get, which is not
// nothing on the trust_background_tail fast path. DB::closeDB prints the
// three [OZONEDB-*-PROFILE] lines to stderr and resets the counters.
//
// Every use site goes through the macros below, which compile to nothing
// when the option is off, so the hot paths carry no #ifdef blocks.
#ifdef OZONEDB_PROFILE_READS
#include <atomic>
#include <chrono>
#include <cstdint>

namespace ozonedb::read_profile {

// DB::get
extern std::atomic<uint64_t> reads;        // gets that went through the full path
extern std::atomic<uint64_t> index_hits;   // gets answered by the key index fast path
extern std::atomic<uint64_t> total_ns;
extern std::atomic<uint64_t> view_ns;      // view snapshot (strict mode: including syncView)
extern std::atomic<uint64_t> log_ns;       // LogHandler::readRecord
extern std::atomic<uint64_t> sst_ns;       // SSTableHandler::readRecordFromAllLevel
extern std::atomic<uint64_t> sst_calls;

// SSTableHandler::readRecordFromAllLevel, indexed by level (0 unused).
constexpr int kMaxLevels = 16;
extern std::atomic<uint64_t> level_ns[kMaxLevels];
extern std::atomic<uint64_t> level_enters[kMaxLevels];         // files looked at
extern std::atomic<uint64_t> level_range_rejects[kMaxLevels];  // skipped on key range
extern std::atomic<uint64_t> level_table_gets[kMaxLevels];     // Table::get calls
extern std::atomic<uint64_t> level_hits[kMaxLevels];           // Table::get found it

// Table::get
extern std::atomic<uint64_t> tbl_file_filter_reject;
extern std::atomic<uint64_t> tbl_block_filter_reject;
extern std::atomic<uint64_t> tbl_index_invalid;    // key past the index, or bad identifier
extern std::atomic<uint64_t> tbl_block_read;       // data block fetched from storage
extern std::atomic<uint64_t> tbl_block_cached;     // data block already in the cache
extern std::atomic<uint64_t> tbl_miss_after_read;  // block consulted, key not there
extern std::atomic<uint64_t> tbl_found;

// Print the three summary lines to stderr and zero every counter.
void dumpAndReset();

// Adds the elapsed nanoseconds to `counter` when destroyed.
struct Scope {
  explicit Scope(std::atomic<uint64_t>& counter) : counter_(counter), t0_(std::chrono::steady_clock::now()) {}
  ~Scope() {
    counter_.fetch_add(std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - t0_).count(),
                       std::memory_order_relaxed);
  }
  std::atomic<uint64_t>& counter_;
  std::chrono::steady_clock::time_point t0_;
};

}  // namespace ozonedb::read_profile

#define OZONEDB_PROFILE_SCOPE(name, counter) ::ozonedb::read_profile::Scope name(counter)
#define OZONEDB_PROFILE_COUNT(counter) (counter).fetch_add(1, std::memory_order_relaxed)
#define OZONEDB_PROFILE_DUMP() ::ozonedb::read_profile::dumpAndReset()
#else
#define OZONEDB_PROFILE_SCOPE(name, counter) ((void)0)
#define OZONEDB_PROFILE_COUNT(counter) ((void)0)
#define OZONEDB_PROFILE_DUMP() ((void)0)
#endif  // OZONEDB_PROFILE_READS
#endif  // OZONEDB_READ_PROFILE_H
