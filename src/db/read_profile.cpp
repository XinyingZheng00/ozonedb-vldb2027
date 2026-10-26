#include "read_profile.h"
#ifdef OZONEDB_PROFILE_READS
#include <cstdio>

namespace ozonedb::read_profile {

std::atomic<uint64_t> reads{0};
std::atomic<uint64_t> index_hits{0};
std::atomic<uint64_t> total_ns{0};
std::atomic<uint64_t> view_ns{0};
std::atomic<uint64_t> log_ns{0};
std::atomic<uint64_t> sst_ns{0};
std::atomic<uint64_t> sst_calls{0};

std::atomic<uint64_t> level_ns[kMaxLevels];
std::atomic<uint64_t> level_enters[kMaxLevels];
std::atomic<uint64_t> level_range_rejects[kMaxLevels];
std::atomic<uint64_t> level_table_gets[kMaxLevels];
std::atomic<uint64_t> level_hits[kMaxLevels];

std::atomic<uint64_t> tbl_file_filter_reject{0};
std::atomic<uint64_t> tbl_block_filter_reject{0};
std::atomic<uint64_t> tbl_index_invalid{0};
std::atomic<uint64_t> tbl_block_read{0};
std::atomic<uint64_t> tbl_block_cached{0};
std::atomic<uint64_t> tbl_miss_after_read{0};
std::atomic<uint64_t> tbl_found{0};

namespace {
uint64_t take(std::atomic<uint64_t>& c) { return c.exchange(0, std::memory_order_relaxed); }
}  // namespace

void dumpAndReset() {
  uint64_t const n = take(reads);
  uint64_t const fast = take(index_hits);
  uint64_t const total = take(total_ns);
  uint64_t const view = take(view_ns);
  uint64_t const log = take(log_ns);
  uint64_t const sst = take(sst_ns);
  uint64_t const calls = take(sst_calls);
  if (n == 0 && fast == 0) {
    for (int i = 0; i < kMaxLevels; ++i) {
      take(level_ns[i]); take(level_enters[i]); take(level_range_rejects[i]); take(level_table_gets[i]); take(level_hits[i]);
    }
    take(tbl_file_filter_reject); take(tbl_block_filter_reject); take(tbl_index_invalid);
    take(tbl_block_read); take(tbl_block_cached); take(tbl_miss_after_read); take(tbl_found);
    return;
  }
  auto pct = [&](uint64_t v) -> double { return total ? 100.0 * v / total : 0.0; };
  auto per_read = [&](uint64_t v) -> double { return n ? v / 1000.0 / n : 0.0; };
  fprintf(stderr,
          "[OZONEDB-PROFILE] reads=%llu index_hits=%llu avg_total=%.2fus view=%.2fus(%.1f%%) "
          "log=%.2fus(%.1f%%) sst=%.2fus_per_call x %llu_calls(%.1f%%_of_total)\n",
          (unsigned long long)n, (unsigned long long)fast, per_read(total),
          per_read(view), pct(view), per_read(log), pct(log),
          calls ? sst / 1000.0 / calls : 0.0, (unsigned long long)calls, pct(sst));
  fprintf(stderr, "[OZONEDB-SST-PROFILE] level | time_total_ms | enters | range_rejects | table_gets | hits\n");
  for (int i = 1; i < kMaxLevels; ++i) {
    uint64_t const enters = take(level_enters[i]);
    uint64_t const ns = take(level_ns[i]);
    uint64_t const rr = take(level_range_rejects[i]);
    uint64_t const tg = take(level_table_gets[i]);
    uint64_t const hits = take(level_hits[i]);
    if (enters == 0) continue;
    fprintf(stderr, "[OZONEDB-SST-PROFILE] L%-2d | %12.2f | %8llu | %12llu | %10llu | %6llu\n",
            i, ns / 1e6, (unsigned long long)enters, (unsigned long long)rr,
            (unsigned long long)tg, (unsigned long long)hits);
  }
  fprintf(stderr,
          "[OZONEDB-TBL-PROFILE] file_filter_reject=%llu block_filter_reject=%llu index_invalid=%llu "
          "block_read=%llu block_cached=%llu miss_after_read=%llu found=%llu\n",
          (unsigned long long)take(tbl_file_filter_reject), (unsigned long long)take(tbl_block_filter_reject),
          (unsigned long long)take(tbl_index_invalid), (unsigned long long)take(tbl_block_read),
          (unsigned long long)take(tbl_block_cached), (unsigned long long)take(tbl_miss_after_read),
          (unsigned long long)take(tbl_found));
  fflush(stderr);
}

}  // namespace ozonedb::read_profile
#endif  // OZONEDB_PROFILE_READS
