// Dead-task detection and takeover through the task log, without a DB: two
// TaskLogHandlers over one FileStorage stand in for two writer processes.
// The heartbeat thread ticks every 100 ms; the timings below are in ticks.
#include "storage.h"
#include "task_log_handler.h"
#include "test_env.h"
#include "gtest/gtest.h"
#include <chrono>
#include <filesystem>
#include <thread>
using namespace ozonedb;

namespace {

TaskRecord makeRecord(std::string const& owner, int generation, TaskRecord::TaskStatus status) {
  TaskRecord r;
  auto* id = r.mutable_task_id();
  id->add_input_files("datalog/datalog0");
  id->add_input_files("datalog/datalog1");
  id->set_compactintonextlevel(true);
  id->set_destinationlevel(1);
  r.set_owner(owner);
  r.set_owner_generation(generation);
  r.set_status(status);
  return r;
}

void sleepTicks(int ticks) {
  std::this_thread::sleep_for(std::chrono::milliseconds(100 * ticks));
}

}  // namespace

class TaskLogTest : public ::testing::Test {
 protected:
  void SetUp() override {
    dir_ = testenv::path("task_log/");
    std::filesystem::remove_all(dir_);
    std::filesystem::create_directories(dir_);
    storage_ = std::make_unique<FileStorage>(dir_);
  }
  std::string dir_;
  std::unique_ptr<FileStorage> storage_;
};

// A claim whose owner never heartbeats is dead after the threshold, and
// a peer claims it at the next owner_generation.
TEST_F(TaskLogTest, dead_task_is_claimed_at_the_next_generation) {
  TaskLogHandler ghost("task.log", storage_.get());
  TaskLogHandler survivor("task.log", storage_.get());
  survivor.setHeartbeatThreshold(3);

  ghost.appendToTaskLog(makeRecord("ghost", 0, TaskRecord::TASK_BEGIN));
  survivor.rollForwardTaskLog();
  EXPECT_FALSE(survivor.getDeadTask().first.IsInitialized()) << "fresh claim must not be dead";

  sleepTicks(6);
  survivor.rollForwardTaskLog();
  auto dead = survivor.getDeadTask();
  ASSERT_TRUE(dead.first.IsInitialized()) << "no heartbeat for twice the threshold";
  EXPECT_EQ(0, dead.second);
  EXPECT_EQ("datalog/datalog0", dead.first.input_files(0));
  EXPECT_FALSE(survivor.getDeadTask().first.IsInitialized()) << "a dead task is handed out once";

  // The takeover claim: generation 1 beats the ghost's generation 0, and a
  // repeat of the ghost's claim at generation 0 is not worth appending.
  TaskRecord claim = makeRecord("survivor", dead.second + 1, TaskRecord::TASK_BEGIN);
  EXPECT_TRUE(survivor.worthAppend(claim));
  EXPECT_FALSE(survivor.worthAppend(makeRecord("late", 0, TaskRecord::TASK_BEGIN)));
  survivor.appendToTaskLog(claim);
  survivor.rollForwardTaskLog();
  EXPECT_TRUE(survivor.isFirstWriterForTask(claim.mutable_task_id(), "survivor", 1));

  // The ghost replays the same log and sees that it lost the task.
  ghost.rollForwardTaskLog();
  EXPECT_FALSE(ghost.isFirstWriterForTask(claim.mutable_task_id(), "ghost", 0));
}

// A task whose owner heartbeats is never declared dead, however often
// the peer replays the log.
TEST_F(TaskLogTest, live_task_is_not_declared_dead) {
  TaskLogHandler owner("task.log", storage_.get());
  TaskLogHandler peer("task.log", storage_.get());
  peer.setHeartbeatThreshold(10);

  owner.appendToTaskLog(makeRecord("owner", 0, TaskRecord::TASK_BEGIN));
  auto const deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(1500);
  while (std::chrono::steady_clock::now() < deadline) {
    owner.appendToTaskLog(makeRecord("owner", 0, TaskRecord::TASK_IN_PROGRESS));
    peer.rollForwardTaskLogIfDue();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }
  peer.rollForwardTaskLog();
  EXPECT_FALSE(peer.getDeadTask().first.IsInitialized());
}

// rollForwardTaskLogIfDue replays at most once per third of the
// threshold; rollForwardTaskLog always replays.
TEST_F(TaskLogTest, periodic_replay_is_paced_by_the_threshold) {
  TaskLogHandler writer("task.log", storage_.get());
  TaskLogHandler peer("task.log", storage_.get());
  peer.setHeartbeatThreshold(30);  // replay interval: 10 ticks

  peer.rollForwardTaskLogIfDue();  // first call always runs
  TaskRecord claim = makeRecord("writer", 0, TaskRecord::TASK_BEGIN);
  writer.appendToTaskLog(claim);
  peer.rollForwardTaskLogIfDue();  // within the interval: no replay
  EXPECT_FALSE(peer.isFirstWriterForTask(claim.mutable_task_id(), "writer", 0));
  peer.rollForwardTaskLog();
  EXPECT_TRUE(peer.isFirstWriterForTask(claim.mutable_task_id(), "writer", 0));
}

// A COMPLETE record ends the task: it is never reported dead afterwards.
TEST_F(TaskLogTest, completed_task_is_never_dead) {
  TaskLogHandler owner("task.log", storage_.get());
  TaskLogHandler peer("task.log", storage_.get());
  peer.setHeartbeatThreshold(2);

  owner.appendToTaskLog(makeRecord("owner", 0, TaskRecord::TASK_BEGIN));
  owner.appendToTaskLog(makeRecord("owner", 0, TaskRecord::TASK_COMPLETE));
  peer.rollForwardTaskLog();
  sleepTicks(5);
  peer.rollForwardTaskLog();
  EXPECT_FALSE(peer.getDeadTask().first.IsInitialized());
}
