// Replay of the metadata log into a View, without a DB. Records go through
// MetadataLogHandler::appendToMetadataLog and rollForwardMetadataLog, the
// path every process takes.
#include "metadata_log_handler.h"
#include "storage.h"
#include "test_env.h"
#include "gtest/gtest.h"
#include <filesystem>
#include <memory>
using namespace ozonedb;

namespace {

OperationRecord logCreate(std::string const& previous_tail, std::string const& new_tail) {
  OperationRecord r;
  r.set_op_type(OperationRecord::LOGCREATE);
  r.add_input_files(previous_tail);
  r.add_output_file(new_tail);
  return r;
}

OperationRecord compact(std::vector<std::string> const& inputs, std::vector<std::string> const& outputs, bool last_level) {
  OperationRecord r;
  r.set_op_type(OperationRecord::COMPACT);
  r.set_compact_in_last_level(last_level);
  for (auto const& in : inputs) r.add_input_files(in);
  for (auto const& out : outputs) {
    r.add_output_file(out);
    r.add_key_start("a");
    r.add_key_end("z");
    r.add_output_bytes(100);
  }
  return r;
}

// Records that put four unversioned files into the last level (sstable2):
// 16 log files, merged pairwise into 8 sstable1 files, merged pairwise into
// sstable2/b0 .. b3. A file without a version prefix is version -1, so the
// first last-level merge emits version 0.
std::vector<OperationRecord> fillLastLevel() {
  std::vector<OperationRecord> records;
  for (int i = 0; i < 16; i++) {
    records.push_back(logCreate(i == 0 ? "datalog/0" : "datalog/" + std::to_string(i - 1), "datalog/" + std::to_string(i)));
  }
  for (int i = 0; i < 8; i++) {
    records.push_back(compact({"datalog/" + std::to_string(2 * i), "datalog/" + std::to_string(2 * i + 1)},
                              {"sstable1/a" + std::to_string(i)}, false));
  }
  for (int i = 0; i < 4; i++) {
    records.push_back(compact({"sstable1/a" + std::to_string(2 * i), "sstable1/a" + std::to_string(2 * i + 1)},
                              {"sstable2/b" + std::to_string(i)}, false));
  }
  return records;
}

OperationRecord const kX = compact({"sstable2/b0", "sstable2/b1"}, {"sstable2/0_c0"}, true);
OperationRecord const kY = compact({"sstable2/b2", "sstable2/b3"}, {"sstable2/0_c1"}, true);
OperationRecord const kZ = compact({"sstable2/0_c0", "sstable2/0_c1"}, {"sstable2/1_d0"}, true);

// One replaying process: a handler over its own copy of the log.
struct Replayer {
  explicit Replayer(std::string const& name) : dir(testenv::path("metadata_log/" + name + "/")) {
    std::filesystem::remove_all(dir);
    std::filesystem::create_directories(dir);
    storage = std::make_unique<FileStorage>(dir);
    handler = std::make_unique<MetadataLogHandler>("metadata.log", storage.get());
  }
  void append(std::vector<OperationRecord> const& records) {
    for (auto const& r : records) handler->appendToMetadataLog(r);
  }
  View roll() { return handler->rollForwardMetadataLog(); }
  std::deque<std::string> lastLevel() { return roll().getWithPrefix("sstable2"); }

  std::string dir;
  std::unique_ptr<FileStorage> storage;
  std::unique_ptr<MetadataLogHandler> handler;
};

}  // namespace

TEST(MetadataLogTest, last_level_layout_is_the_same_in_either_order) {
  Replayer xy("xy"), yx("yx"), yx_batched("yx_batched");

  xy.append(fillLastLevel());
  xy.append({kX, kY});
  std::deque<std::string> const expected = {"sstable2/0_c0", "sstable2/0_c1"};
  EXPECT_EQ(expected, xy.lastLevel());

  yx.append(fillLastLevel());
  yx.append({kY, kX});
  EXPECT_EQ(expected, yx.lastLevel());

  // This process replays Y before X is appended: Y waits in the buffer
  // and applies once X arrives.
  yx_batched.append(fillLastLevel());
  yx_batched.append({kY});
  EXPECT_EQ((std::deque<std::string>{"sstable2/b0", "sstable2/b1", "sstable2/b2", "sstable2/b3"}), yx_batched.lastLevel());
  yx_batched.append({kX});
  EXPECT_EQ(expected, yx_batched.lastLevel());

  // The next round merges the two version-0 files into one version-1 file.
  for (Replayer* r : {&xy, &yx, &yx_batched}) {
    r->append({kZ});
    View v = r->roll();
    EXPECT_EQ((std::deque<std::string>{"sstable2/1_d0"}), v.getWithPrefix("sstable2"));
    EXPECT_EQ(1u, v.key_range.count("sstable2/1_d0"));
    EXPECT_EQ(0u, v.key_range.count("sstable2/0_c0"));
    EXPECT_EQ(100u, v.getFileSize("sstable2/1_d0"));
  }
}

// A COMPACT whose inputs are gone (a duplicate record) is parked, not
// applied: the layout is unchanged and replay continues.
TEST(MetadataLogTest, record_with_absent_inputs_is_parked) {
  Replayer r("dup");
  r.append(fillLastLevel());
  r.append({kX, kY, kX});
  std::deque<std::string> const expected = {"sstable2/0_c0", "sstable2/0_c1"};
  EXPECT_EQ(expected, r.lastLevel());
  // Later records still apply around the parked one.
  r.append({kZ});
  EXPECT_EQ((std::deque<std::string>{"sstable2/1_d0"}), r.lastLevel());
}

// Non-last-level COMPACT records also apply in input order: the record
// for the second pair waits until the first pair's record arrives.
TEST(MetadataLogTest, non_last_level_record_waits_for_the_front_of_the_level) {
  Replayer r("l1");
  std::vector<OperationRecord> records;
  for (int i = 0; i < 4; i++) {
    records.push_back(logCreate(i == 0 ? "datalog/0" : "datalog/" + std::to_string(i - 1), "datalog/" + std::to_string(i)));
  }
  r.append(records);
  r.append({compact({"datalog/2", "datalog/3"}, {"sstable1/a1"}, false)});
  EXPECT_EQ((std::deque<std::string>{"datalog/0", "datalog/1", "datalog/2", "datalog/3"}), r.roll().getWithPrefix("datalog"));
  r.append({compact({"datalog/0", "datalog/1"}, {"sstable1/a0"}, false)});
  View v = r.roll();
  EXPECT_TRUE(v.getWithPrefix("datalog").empty());
  EXPECT_EQ((std::deque<std::string>{"sstable1/a0", "sstable1/a1"}), v.getWithPrefix("sstable1"));
}
