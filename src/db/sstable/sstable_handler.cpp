#include "sstable/sstable_handler.h"
#include "read_profile.h"
#include <algorithm>
#include "sstable/table_builder.h"
#include "sstable/table_reader.h"

namespace ozonedb {

Status SSTableHandler::readRecordFromAllLevel(std::string const& key, std::shared_ptr<Record>& record, std::string const& offset) {
  // A level prefix is sstable_prefix + level number.

  // latest_view is null until the first View is published.
  // lru_cache is set after construction.
  if (this->latest_view == nullptr || this->lru_cache == nullptr) {
    return Status::kFailure;
  }

  for (int i = 1; i <= max_level; i++) {
    OZONEDB_PROFILE_SCOPE(profile_level, read_profile::level_ns[std::min(i, read_profile::kMaxLevels - 1)]);
    std::string prefix = this->sstable_prefix + std::to_string(i);

    // tables aliases the View snapshot; the caller holds the snapshot
    // until DB::get returns. Do not copy it.
    std::deque<std::string> const& tables = this->latest_view->getWithPrefix(prefix);
    std::shared_mutex record_mutex;
    int finished_threads = 0;
    int record_file = -1;
    std::mutex cv_mutex;
    std::condition_variable cv;

    int count = 0;
    for (int j = tables.size() - 1; j >= 0; j--) {
      std::string const& file_name = tables[j];
      OZONEDB_PROFILE_COUNT(read_profile::level_enters[std::min(i, read_profile::kMaxLevels - 1)]);
      if (key < this->latest_view->getKeyRange(file_name).first || key > this->latest_view->getKeyRange(file_name).second) {
        OZONEDB_PROFILE_COUNT(read_profile::level_range_rejects[std::min(i, read_profile::kMaxLevels - 1)]);
        continue;
      }
      Table* table = nullptr;
      this->lru_cache->getSSTable(file_name, table);
      // getSSTable returns null when Table::open failed or threw, or when
      // the single-flight leader failed. Skip the file.
      if (table == nullptr) continue;
      OZONEDB_PROFILE_COUNT(read_profile::level_table_gets[std::min(i, read_profile::kMaxLevels - 1)]);
      Status status = table->get(key, record);
      if (status == Status::kSuccess) {
        OZONEDB_PROFILE_COUNT(read_profile::level_hits[std::min(i, read_profile::kMaxLevels - 1)]);
        return Status::kSuccess;
      }
      if (offset == file_name) {
        goto outer;
      }
    }
  outer:
    if (record) {
      return Status::kSuccess;
    }
  }
  return Status::kFailure;
}

}  // namespace ozonedb