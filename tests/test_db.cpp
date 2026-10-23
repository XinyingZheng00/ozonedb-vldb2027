#include "db.h"
#include "protobuf_serializer.h"
#include "storage.h"
#include "test_env.h"
#include <algorithm>
#include <filesystem>
#include <thread>
#include "gtest/gtest.h"
#include <chrono>
using namespace ozonedb;

class DBTest : public ::testing::Test {
 protected:
  void SetUp() override {
    // This will run before each test
    std::filesystem::remove_all(testenv::path("db/"));
  }

  // You can also add a TearDown method if needed
  void TearDown() override {
    // Code here will be called after each test
  }
};

TEST_F(DBTest, open) {
  DB* db = nullptr;

  std::string shared_config_path = testenv::dbConfig();

  Status status = DB::openDB(db, shared_config_path);
  EXPECT_EQ(Status::kSuccess, status);

  DB::closeDB(db);
}

TEST_F(DBTest, single_put_get) {
  DB* db = nullptr;

  std::string shared_config_path = testenv::dbConfig();

  Status status = DB::openDB(db, shared_config_path);
  EXPECT_EQ(Status::kSuccess, status);

  status = db->put("key", "value");
  EXPECT_EQ(Status::kSuccess, status);
  DB::closeDB(db);

  DB::openDB(db, shared_config_path);
  std::string const* value = nullptr;
  std::shared_ptr<Record> guard;
  status = db->get("key", value, guard);
  EXPECT_EQ(Status::kSuccess, status);
  EXPECT_EQ("value", *value);

  DB::closeDB(db);
}

TEST_F(DBTest, multiple_put_get) {
  DB* db = nullptr;

  std::string shared_config_path = testenv::dbConfig();

  Status status = DB::openDB(db, shared_config_path);
  EXPECT_EQ(Status::kSuccess, status);

  for (size_t i = 0; i < 10; i++) {
    status = db->put("key" + std::to_string(i), "value" + std::to_string(i));
    EXPECT_EQ(Status::kSuccess, status);
  }
  DB::closeDB(db);

  DB::openDB(db, shared_config_path);
  for (size_t i = 0; i < 10; i++) {
    std::string const* value = nullptr;
    std::shared_ptr<Record> guard;
    auto start = std::chrono::high_resolution_clock::now();
    status = db->get("key" + std::to_string(i), value, guard);
    auto end = std::chrono::high_resolution_clock::now();
    std::cout << "Time taken to get the value " << i << " : " << std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count() << "ns" << std::endl;
    EXPECT_EQ(Status::kSuccess, status);
    EXPECT_EQ("value" + std::to_string(i), *value);
  }

  DB::closeDB(db);
}

TEST_F(DBTest, put_after_put_return_new_value) {
  DB* db = nullptr;

  std::string shared_config_path = testenv::dbConfig();

  Status status = DB::openDB(db, shared_config_path);
  EXPECT_EQ(Status::kSuccess, status);

  status = db->put("key", "value");
  EXPECT_EQ(Status::kSuccess, status);

  status = db->put("key", "new_value");
  EXPECT_EQ(Status::kSuccess, status);
  DB::closeDB(db);

  DB::openDB(db, shared_config_path);
  std::string const* value = nullptr;
  std::shared_ptr<Record> guard;
  status = db->get("key", value, guard);
  EXPECT_EQ(Status::kSuccess, status);
  EXPECT_EQ("new_value", *value);

  DB::closeDB(db);
}

TEST_F(DBTest, delete_after_put) {
  DB* db = nullptr;

  std::string shared_config_path = testenv::dbConfig();

  Status status = DB::openDB(db, shared_config_path);
  EXPECT_EQ(Status::kSuccess, status);

  status = db->put("key", "value");
  EXPECT_EQ(Status::kSuccess, status);
  status = db->remove("key");
  EXPECT_EQ(Status::kSuccess, status);
  DB::closeDB(db);

  DB::openDB(db, shared_config_path);
  std::string const* value = nullptr;
  std::shared_ptr<Record> guard;
  status = db->get("key", value, guard);
  EXPECT_EQ(Status::kFailure, status);

  DB::closeDB(db);
}

TEST_F(DBTest, large_put) {
  DB* db = nullptr;

  std::string shared_config_path = testenv::dbConfig();

  Status status = DB::openDB(db, shared_config_path);
  EXPECT_EQ(Status::kSuccess, status);

  for (size_t i = 0; i < 100; i++) {
    status = db->put("key" + std::to_string(i), "value" + std::to_string(i));
    EXPECT_EQ(Status::kSuccess, status);
  }
  DB::closeDB(db);
}

TEST_F(DBTest, values_survive_reopen) {
  DB* db = nullptr;

  std::string shared_config_path = testenv::dbConfig();

  Status status = DB::openDB(db, shared_config_path);
  EXPECT_EQ(Status::kSuccess, status);

  for (size_t i = 0; i < 100; i++) {
    status = db->put("key" + std::to_string(i), "value" + std::to_string(i));
    EXPECT_EQ(Status::kSuccess, status);
  }
  DB::closeDB(db);

  DB::openDB(db, shared_config_path);
  for (size_t i = 0; i < 10; i++) {
    std::string const* value = nullptr;
    std::shared_ptr<Record> guard;
    status = db->get("key" + std::to_string(i * 10), value, guard);
    EXPECT_EQ(Status::kSuccess, status);
    EXPECT_EQ("value" + std::to_string(i * 10), *value);
  }
  for (size_t i = 0; i < 10; i++) {
    status = db->put("key" + std::to_string(i * 10), "value_new" + std::to_string(i * 10));
    EXPECT_EQ(Status::kSuccess, status);
  }
  DB::closeDB(db);

  DB::openDB(db, shared_config_path);
  for (size_t i = 0; i < 10; i++) {
    std::string const* value = nullptr;
    std::shared_ptr<Record> guard;
    status = db->get("key" + std::to_string(i * 10), value, guard);
    EXPECT_EQ(Status::kSuccess, status);
    EXPECT_EQ("value_new" + std::to_string(i * 10), *value);
  }
  std::string const* value = nullptr;
  std::shared_ptr<Record> guard;
  status = db->get("key100", value, guard);
  EXPECT_EQ(Status::kFailure, status);

  DB::closeDB(db);
}

TEST_F(DBTest, reopen_read_latency) {
  DB* db = nullptr;

  std::string shared_config_path = testenv::dbConfig();

  Status status = DB::openDB(db, shared_config_path);
  EXPECT_EQ(Status::kSuccess, status);

  for (size_t i = 0; i < 55; i++) {
    status = db->put("key" + std::to_string(i), "value" + std::to_string(i));
    EXPECT_EQ(Status::kSuccess, status);
  }
  DB::closeDB(db);

  DB::openDB(db, shared_config_path);
  for (size_t i = 0; i < 10; i++) {
    std::string const* value = nullptr;
    std::shared_ptr<Record> guard;
    auto start = std::chrono::high_resolution_clock::now();
    status = db->get("key" + std::to_string(i), value, guard);
    auto end = std::chrono::high_resolution_clock::now();
    std::chrono::duration<double> elapsed_time = end - start;
    std::cout << "Time taken to get the value: " << elapsed_time.count() << "s" << std::endl;
    EXPECT_EQ(Status::kSuccess, status);
    EXPECT_EQ("value" + std::to_string(i), *value);
  }
  for (size_t i = 0; i < 10; i++) {
    std::string const* value = nullptr;
    std::shared_ptr<Record> guard;
    auto start = std::chrono::high_resolution_clock::now();
    status = db->get("key" + std::to_string(i), value, guard);
    auto end = std::chrono::high_resolution_clock::now();
    std::chrono::duration<double> elapsed_time = end - start;
    std::cout << "Time taken to get the value: " << elapsed_time.count() << "s" << std::endl;
    EXPECT_EQ(Status::kSuccess, status);
    EXPECT_EQ("value" + std::to_string(i), *value);
  }

  DB::closeDB(db);
}

namespace {
// Statuses of every TaskRecord in <db_dir>/task.log, in append order.
std::vector<TaskRecord::TaskStatus> taskLogStatuses(std::string const& db_dir) {
  std::vector<TaskRecord::TaskStatus> out;
  FileStorage storage(db_dir);
  if (!storage.exist("task.log")) return out;
  unsigned char* buffer = nullptr;
  size_t size = 0;
  if (storage.read("task.log", buffer, size) != Status::kSuccess || buffer == nullptr) return out;
  std::vector<google::protobuf::Message*> records;
  protobuf::deserializeMessages(buffer, size, records, []() -> google::protobuf::Message* { return new TaskRecord(); });
  delete[] buffer;
  for (auto* m : records) {
    out.push_back(static_cast<TaskRecord*>(m)->status());
    delete m;
  }
  return out;
}

int countStatus(std::vector<TaskRecord::TaskStatus> const& v, TaskRecord::TaskStatus s) {
  return static_cast<int>(std::count(v.begin(), v.end(), s));
}

// Put `count` keys in rounds with a pause between rounds. The data log
// rolls on a tail size the view thread refreshes every 100 ms, so a burst
// of puts that finishes inside one refresh lands in ONE log file however
// small the limit (on a tmpfs test root all 300 puts below did), and a
// test that needs several log files, and so a compaction, then has none.
// Each round here exceeds the 1 KiB limit of test_db.json on its own.
void putInRounds(DB* db, int count, std::string const& value, int per_round = 25) {
  for (int i = 0; i < count; i++) {
    ASSERT_EQ(Status::kSuccess, db->put("key" + std::to_string(i), value)) << "key" << i;
    if ((i + 1) % per_round == 0) std::this_thread::sleep_for(std::chrono::milliseconds(150));
  }
}
}  // namespace

// Fault injection: with compaction_abort_rate = 1 every claimed task is
// abandoned, so the task log holds claims (at rising generations, as the
// abandoned ones are reclaimed) but never a TASK_COMPLETE, and every key
// stays readable from the log files. Reopened with the knob off and a
// short threshold, the stale claims lapse and compaction finishes.
TEST_F(DBTest, abort_rate_one_leaves_claims_unfinished_and_data_readable) {
  std::string const name = "db_abort";
  std::filesystem::remove_all(testenv::path(name + "/"));
  std::string const abort_cfg = testenv::dbConfigWith(name, {{"compaction_abort_rate", "1.0"},
                                                             {"task_heartbeat_threshold_ticks", "5"}});
  DB* db = nullptr;
  ASSERT_EQ(Status::kSuccess, DB::openDB(db, abort_cfg));
  std::string const value(100, 'v');
  putInRounds(db, 300, value);
  // Let the watcher claim, abort, and reclaim for a while.
  std::this_thread::sleep_for(std::chrono::milliseconds(1500));
  for (int i = 0; i < 300; i++) {
    std::string const* v = nullptr;
    std::shared_ptr<Record> guard;
    ASSERT_EQ(Status::kSuccess, db->get("key" + std::to_string(i), v, guard)) << "key" << i;
    EXPECT_EQ(value, *v);
  }
  DB::closeDB(db);

  auto statuses = taskLogStatuses(testenv::path(name + "/"));
  EXPECT_GT(countStatus(statuses, TaskRecord::TASK_BEGIN), 0) << "no compaction was ever claimed";
  EXPECT_EQ(0, countStatus(statuses, TaskRecord::TASK_COMPLETE)) << "an aborted task must not complete";

  // Knob off: the abandoned claims lapse and the backlog drains.
  std::string const normal_cfg = testenv::dbConfigWith(name, {{"task_heartbeat_threshold_ticks", "5"}});
  ASSERT_EQ(Status::kSuccess, DB::openDB(db, normal_cfg));
  bool completed = false;
  for (int i = 0; i < 100 && !completed; i++) {
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    completed = countStatus(taskLogStatuses(testenv::path(name + "/")), TaskRecord::TASK_COMPLETE) > 0;
  }
  EXPECT_TRUE(completed) << "no task completed within 10 s of reopening with the knob off";
  for (int i = 0; i < 300; i++) {
    std::string const* v = nullptr;
    std::shared_ptr<Record> guard;
    ASSERT_EQ(Status::kSuccess, db->get("key" + std::to_string(i), v, guard)) << "key" << i;
  }
  DB::closeDB(db);
}

// compaction_enabled = false opens a DB that never compacts: the log
// files pile up and stay readable, and the task log stays empty.
TEST_F(DBTest, compaction_disabled_leaves_the_log_files_alone) {
  std::string const name = "db_nocompact";
  std::filesystem::remove_all(testenv::path(name + "/"));
  std::string const cfg = testenv::dbConfigWith(name, {{"compaction_enabled", "false"}});
  DB* db = nullptr;
  ASSERT_EQ(Status::kSuccess, DB::openDB(db, cfg));
  std::string const value(100, 'v');
  putInRounds(db, 300, value);
  std::this_thread::sleep_for(std::chrono::milliseconds(800));
  for (int i = 0; i < 300; i++) {
    std::string const* v = nullptr;
    std::shared_ptr<Record> guard;
    ASSERT_EQ(Status::kSuccess, db->get("key" + std::to_string(i), v, guard)) << "key" << i;
  }
  DB::closeDB(db);
  EXPECT_TRUE(taskLogStatuses(testenv::path(name + "/")).empty());
  int log_files = 0;
  for (auto const& e : std::filesystem::directory_iterator(testenv::path(name + "/datalog/"))) {
    (void)e;
    ++log_files;
  }
  EXPECT_GT(log_files, 2) << "300 x 100 B puts in rounds at a 1 KiB log limit must leave many log files";
}

// Last-level compactions merge two same-version files into one file of
// the next version (sstable3/1_<name>.sst from two 0_ files), and the
// level converges to at most last_file_number_limit files. Every key stays
// readable throughout.
TEST_F(DBTest, last_level_compaction_emits_one_versioned_output) {
  std::string const name = "db_lastlevel";
  std::filesystem::remove_all(testenv::path(name + "/"));
  std::string const cfg = testenv::dbConfigWith(name, {});
  DB* db = nullptr;
  ASSERT_EQ(Status::kSuccess, DB::openDB(db, cfg));
  std::string const value(100, 'v');
  putInRounds(db, 3000, value, 50);
  // Wait for the tree to settle: no more than last_file_number_limit (4)
  // files at the last level, and at least one of version >= 1.
  std::string const last_level = testenv::path(name + "/sstable3/");
  int highest_version = -1;
  size_t files = 0;
  for (int tick = 0; tick < 300; tick++) {
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    if (!std::filesystem::exists(last_level)) continue;
    highest_version = -1;
    files = 0;
    for (auto const& e : std::filesystem::directory_iterator(last_level)) {
      ++files;
      std::string const fn = e.path().filename().string();
      size_t us = fn.find('_');
      if (us != std::string::npos) highest_version = std::max(highest_version, std::stoi(fn.substr(0, us)));
    }
    if (highest_version >= 1 && files <= 4) break;
  }
  EXPECT_GE(highest_version, 1) << "no second-round last-level merge within 30 s";
  EXPECT_LE(files, 4u) << "last level did not converge to last_file_number_limit files";
  for (int i = 0; i < 3000; i++) {
    std::string const* v = nullptr;
    std::shared_ptr<Record> guard;
    ASSERT_EQ(Status::kSuccess, db->get("key" + std::to_string(i), v, guard)) << "key" << i;
    EXPECT_EQ(value, *v);
  }
  DB::closeDB(db);
}

