// S3Storage against a live S3-compatible server (MinIO in practice). The
// suites self-skip unless S3_TEST_ENDPOINT is set; bench/scripts/minio_local.sh
// test starts a server and sets it.
//
//   S3_TEST_ENDPOINT    e.g. http://127.0.0.1:9000
//   S3_TEST_BUCKET      default ozonedb-sstables; must already exist
//   S3_TEST_ACCESS_KEY  default minioadmin
//   S3_TEST_SECRET_KEY  default minioadmin
//
// Off EC2, also set AWS_EC2_METADATA_DISABLED=true: the SDK otherwise
// probes the instance-metadata endpoint for every client.
//
// Every test writes under its own key prefix, so reruns do not collide.
#ifdef OZONEDB_ENABLE_S3
#include "db.h"
#include "s3_storage.h"
#include "test_env.h"
#include "gtest/gtest.h"
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <string>
#include <thread>
#include <unistd.h>
#include <vector>

using namespace ozonedb;

namespace {

std::string envOr(char const* name, std::string const& fallback) {
  char const* e = std::getenv(name);
  return (e && *e) ? std::string(e) : fallback;
}

struct S3Env {
  static std::string endpoint() { return envOr("S3_TEST_ENDPOINT", ""); }
  static std::string bucket() { return envOr("S3_TEST_BUCKET", "ozonedb-sstables"); }
  static std::string accessKey() { return envOr("S3_TEST_ACCESS_KEY", "minioadmin"); }
  static std::string secretKey() { return envOr("S3_TEST_SECRET_KEY", "minioadmin"); }
  static bool available() { return !endpoint().empty(); }

  // s3test/<pid>_<time>/<tag>/ -- unique per test and per run.
  static std::string prefix(std::string const& tag) {
    return "s3test/" + std::to_string(getpid()) + "_" + std::to_string(time(nullptr)) + "/" + tag + "/";
  }
};

#define SKIP_IF_NO_S3()                                              \
  do {                                                               \
    if (!S3Env::available()) {                                       \
      GTEST_SKIP() << "S3_TEST_ENDPOINT not set; skipping S3 test"; \
    }                                                                \
  } while (0)

std::unique_ptr<S3Storage> makeStorage(std::string const& prefix) {
  return std::make_unique<S3Storage>(S3Env::endpoint(), "us-east-1", S3Env::bucket(),
                                     S3Env::accessKey(), S3Env::secretKey(),
                                     /*use_path_style=*/true, prefix);
}

// Read through a second client, which has no local write buffer: what it
// sees is what the server holds.
std::vector<uint8_t> readAll(std::string const& prefix, std::string const& file) {
  auto s = makeStorage(prefix);
  unsigned char* data = nullptr;
  size_t size = 0;
  std::vector<uint8_t> out;
  if (s->read(file, data, size) == Status::kSuccess) out.assign(data, data + size);
  delete[] data;
  return out;
}

}  // namespace

TEST(S3StorageTest, append_and_read_back) {
  SKIP_IF_NO_S3();
  std::string prefix = S3Env::prefix("rw");
  auto storage = makeStorage(prefix);

  std::vector<uint8_t> data = {1, 2, 3, 4, 5};
  ASSERT_EQ(Status::kSuccess, storage->append("file1", data.data(), data.size()));
  EXPECT_EQ(data, readAll(prefix, "file1"));

  // A second append rewrites the object with the whole buffer.
  ASSERT_EQ(Status::kSuccess, storage->append("file1", data.data(), data.size()));
  std::vector<uint8_t> twice = data;
  twice.insert(twice.end(), data.begin(), data.end());
  EXPECT_EQ(twice, readAll(prefix, "file1"));
}

TEST(S3StorageTest, ranged_read) {
  SKIP_IF_NO_S3();
  std::string prefix = S3Env::prefix("range");
  auto storage = makeStorage(prefix);

  std::vector<uint8_t> data(1000);
  for (size_t i = 0; i < data.size(); ++i) data[i] = static_cast<uint8_t>(i * 7);
  ASSERT_EQ(Status::kSuccess, storage->append("file", data.data(), data.size()));

  auto reader = makeStorage(prefix);
  unsigned char* out = nullptr;
  ASSERT_EQ(Status::kSuccess, reader->read("file", out, 250, 100));
  EXPECT_EQ(std::vector<uint8_t>(data.begin() + 250, data.begin() + 350), std::vector<uint8_t>(out, out + 100));
  delete[] out;
}

TEST(S3StorageTest, batch_is_invisible_until_flush) {
  SKIP_IF_NO_S3();
  std::string prefix = S3Env::prefix("batch");
  auto storage = makeStorage(prefix);

  std::vector<uint8_t> expected;
  for (int i = 0; i < 3; ++i) {
    std::vector<uint8_t> chunk(64, static_cast<uint8_t>(i));
    ASSERT_EQ(Status::kSuccess, storage->appendInBatch("file", chunk.data(), static_cast<int>(chunk.size())));
    expected.insert(expected.end(), chunk.begin(), chunk.end());
  }
  // Buffered locally: this client sees it, the server does not have it yet.
  EXPECT_TRUE(storage->exist("file"));
  EXPECT_EQ(expected.size(), storage->size("file"));
  EXPECT_FALSE(makeStorage(prefix)->exist("file"));

  ASSERT_EQ(Status::kSuccess, storage->flush("file"));
  EXPECT_EQ(expected, readAll(prefix, "file"));
  EXPECT_EQ(expected.size(), makeStorage(prefix)->size("file"));
}

TEST(S3StorageTest, missing_object) {
  SKIP_IF_NO_S3();
  auto storage = makeStorage(S3Env::prefix("missing"));
  EXPECT_FALSE(storage->exist("nope"));
  EXPECT_EQ(0u, storage->size("nope"));
  unsigned char* data = nullptr;
  size_t size = 0;
  EXPECT_EQ(Status::kFailure, storage->read("nope", data, size));
}

TEST(S3StorageTest, seal_returns_kSealed_on_further_append) {
  SKIP_IF_NO_S3();
  std::string prefix = S3Env::prefix("seal");
  auto storage = makeStorage(prefix);

  std::vector<uint8_t> data = {9, 8, 7};
  ASSERT_EQ(Status::kSuccess, storage->appendInBatch("file", data.data(), static_cast<int>(data.size())));
  storage->seal("file");  // flushes the buffer first
  EXPECT_TRUE(storage->isSealed("file"));
  EXPECT_EQ(Status::kSealed, storage->append("file", data.data(), data.size()));
  EXPECT_EQ(data, readAll(prefix, "file"));
}

TEST(S3StorageTest, remove_deletes_the_object) {
  SKIP_IF_NO_S3();
  std::string prefix = S3Env::prefix("remove");
  auto storage = makeStorage(prefix);

  std::vector<uint8_t> data = {1, 2, 3};
  ASSERT_EQ(Status::kSuccess, storage->append("file", data.data(), data.size()));
  ASSERT_TRUE(makeStorage(prefix)->exist("file"));
  storage->remove("file");
  EXPECT_FALSE(storage->exist("file"));
  EXPECT_FALSE(makeStorage(prefix)->exist("file"));
}

namespace {

class CompactionCounter : public EventListener {
 public:
  std::atomic<int> log_compactions{0};
  void onLogCompactionCompletion(int) override { ++log_compactions; }
};

// Small limits, so a few hundred puts compact. The log is on local files
// and the SSTables are in the bucket under `sstable_dir`.
std::string writeS3DBConfig(std::string const& sstable_dir) {
  std::string const file = testenv::path("s3_db.json");
  std::ofstream(file) << "{\n"
                      << "  \"db_path\": \"" << testenv::path("s3db/") << "\",\n"
                      << "  \"mode\": 1,\n"
                      << "  \"log_file_size_limit\": 1024,\n"
                      << "  \"base_file_number_limit\": 4,\n"
                      << "  \"level_size\": \"[4096, 40960, 409600]\",\n"
                      << "  \"level_file_size_limit\": \"[4096, 40960, 409600]\",\n"
                      << "  \"max_level\": 3,\n"
                      << "  \"compaction_policy\": 0,\n"
                      << "  \"sstable_backend\": \"s3\",\n"
                      << "  \"s3_endpoint\": \"" << S3Env::endpoint() << "\",\n"
                      << "  \"s3_region\": \"us-east-1\",\n"
                      << "  \"s3_bucket\": \"" << S3Env::bucket() << "\",\n"
                      << "  \"s3_access_key\": \"" << S3Env::accessKey() << "\",\n"
                      << "  \"s3_secret_key\": \"" << S3Env::secretKey() << "\",\n"
                      << "  \"s3_use_path_style\": \"true\",\n"
                      << "  \"sstable_dir\": \"" << sstable_dir << "\"\n"
                      << "}\n";
  return file;
}

}  // namespace

// The split of paper §3.5: log on one backend, SSTables in the object
// store. Puts enough to force log compactions, which write SSTables to S3,
// then reads every key back after a reopen.
//
// The puts come in rounds: the data log rolls on the tail size the View
// holds, which a background thread refreshes every 100 ms. A burst of puts
// lands in one log file and leaves nothing to compact.
TEST(S3DBTest, sstables_round_trip_through_s3) {
  SKIP_IF_NO_S3();
  std::filesystem::remove_all(testenv::path("s3db/"));
  std::string config = writeS3DBConfig(S3Env::prefix("db"));
  int const rounds = 10;
  int const per_round = 50;
  int const n = rounds * per_round;

  DB* db = nullptr;
  ASSERT_EQ(Status::kSuccess, DB::openDB(db, config));
  CompactionCounter counter;
  db->setEventListener(&counter);
  for (int i = 0; i < n; ++i) {
    ASSERT_EQ(Status::kSuccess, db->put("key" + std::to_string(i), "value" + std::to_string(i)));
    if (i % per_round == per_round - 1) std::this_thread::sleep_for(std::chrono::milliseconds(250));
  }
  for (int i = 0; i < 100 && counter.log_compactions == 0; ++i) {
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  EXPECT_GT(counter.log_compactions.load(), 0) << "no log compaction wrote an SSTable";
  DB::closeDB(db);

  ASSERT_EQ(Status::kSuccess, DB::openDB(db, config));
  for (int i = 0; i < n; ++i) {
    std::string const* value = nullptr;
    std::shared_ptr<Record> guard;
    ASSERT_EQ(Status::kSuccess, db->get("key" + std::to_string(i), value, guard)) << "key" << i;
    EXPECT_EQ("value" + std::to_string(i), *value);
  }
  DB::closeDB(db);
}

#endif  // OZONEDB_ENABLE_S3
