#ifndef TASK_LOG_HANDLER_H
#define TASK_LOG_HANDLER_H
#include "protobuf/record.pb.h"
#include "storage.h"
#include <chrono>
#include <cstring>
#include <thread>
#include <unordered_map>

/*
In the very basic case, we can proof, the compaction is performed at most f times
with (f-1) failure in the system.
*/

namespace ozonedb {

// Define a custom hash function for TaskIdentifier
struct TaskIdentifierHash {
  std::size_t operator()(TaskRecord::TaskIdentifier const& task_id) const {
    std::size_t seed = 0;
    for (auto const& file : task_id.input_files()) {
      seed ^= std::hash<std::string>{}(file) + 0x9e3779b9 + (seed << 6) + (seed >> 2);
    }
    return seed;
  }
};

// Define equality operator for TaskIdentifier
struct TaskIdentifierEqual {
  bool operator()(TaskRecord::TaskIdentifier const& lhs, TaskRecord::TaskIdentifier const& rhs) const {
    // Compare the repeated fields of input_files
    if (lhs.input_files_size() != rhs.input_files_size()) {
      return false;  // Different sizes mean they are not equal
    }

    for (int i = 0; i < lhs.input_files_size(); ++i) {
      if (lhs.input_files(i) != rhs.input_files(i)) {
        return false;  // Found a mismatch in elements
      }
    }
    return true;  // All elements matched
  }
};

class TaskLogHandler {
  struct TaskStatus {
    std::string owner;
    int heartbeat;
    int owner_generation;
    TaskRecord::TaskStatus status;
  };

 private:
  // Bytes of task.log already replayed into task_map. Guarded by
  // task_log_mutex; see readTaskLog for why the I/O itself is not.
  size_t offset;
  std::string active_unit;
  Storage* storage = nullptr;
  // When rollForwardTaskLog last ran; rollForwardTaskLogIfDue paces on it.
  // Both are called from the compaction thread only.
  std::chrono::steady_clock::time_point last_rollforward_{};

  std::unordered_map<TaskRecord::TaskIdentifier, TaskStatus, TaskIdentifierHash, TaskIdentifierEqual> task_map;
  std::shared_mutex task_map_mutex;

  std::unordered_map<TaskRecord::TaskIdentifier, int, TaskIdentifierHash, TaskIdentifierEqual> dead_tasks;  // the value here is used to store the owner generation
  // In 100 ms ticks (updateHeartbeats). Set from Metadata's
  // task_heartbeat_threshold_ticks by DB::DB; the default matches it. The
  // original literal was 10,000,000 (11.6 days), which left the dead-task
  // path unreachable.
  int task_heartbeat_threshold = 30;
  std::shared_mutex dead_tasks_mutex;
  std::shared_mutex task_log_mutex;

  Status readTaskLog(std::vector<TaskRecord*>& result);
  void rollforwardSingleTask(TaskRecord const& task_record);

  void updateHeartbeats();
  void startHeartbeatThread();
  void stopHeartbeatThread();
  std::thread heartbeat_thread;
  bool stop_thread = false;

 public:
  TaskLogHandler(std::string const& task_log, Storage* storage) {
    this->offset = 0;
    this->storage = storage;
    this->active_unit = task_log;
    startHeartbeatThread();
  };

  ~TaskLogHandler() {
    stopHeartbeatThread();
  };

  // Replay every record appended since the last replay, then mark as dead
  // every task whose tick count reached the threshold. Replaying a task's
  // own heartbeats is what resets its count, so this is the only place
  // dead tasks are discovered, and it has to run periodically, not only
  // when this writer attempts a claim.
  void rollForwardTaskLog();
  // rollForwardTaskLog, at most once per third of the threshold. The
  // compaction thread calls it on every pick: a replay is cheap on a local
  // file and two fenced calls on Corfu, and a live task's count is reset
  // by each replay, so this interval keeps it under a third of the
  // threshold plus the replay latency.
  void rollForwardTaskLogIfDue();

  // Ticks without a heartbeat after which a task counts as dead. Call
  // before the first rollForwardTaskLog.
  void setHeartbeatThreshold(int ticks) { this->task_heartbeat_threshold = ticks; }
  int heartbeatThreshold() const { return this->task_heartbeat_threshold; }

  bool isFirstWriterForTask(TaskRecord::TaskIdentifier* task_id, std::string const& client, int owner_generation);
  bool worthAppend(TaskRecord const& record);

  void appendToTaskLog(TaskRecord const& record);

  std::pair<TaskRecord::TaskIdentifier, int> getDeadTask();
};
}  // namespace ozonedb
#endif
