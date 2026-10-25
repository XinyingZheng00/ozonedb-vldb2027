#ifndef TASK_LOG_HANDLER_H
#define TASK_LOG_HANDLER_H
#include "protobuf/record.pb.h"
#include "storage.h"
#include <chrono>
#include <cstring>
#include <thread>
#include <unordered_map>

/*
With f-1 writer failures, a compaction runs at most f times.
*/

namespace ozonedb {

// Hash of a TaskIdentifier over its input files.
struct TaskIdentifierHash {
  std::size_t operator()(TaskRecord::TaskIdentifier const& task_id) const {
    std::size_t seed = 0;
    for (auto const& file : task_id.input_files()) {
      seed ^= std::hash<std::string>{}(file) + 0x9e3779b9 + (seed << 6) + (seed >> 2);
    }
    return seed;
  }
};

// Two TaskIdentifiers are equal when they list the same input files in the same order.
struct TaskIdentifierEqual {
  bool operator()(TaskRecord::TaskIdentifier const& lhs, TaskRecord::TaskIdentifier const& rhs) const {
    if (lhs.input_files_size() != rhs.input_files_size()) {
      return false;
    }

    for (int i = 0; i < lhs.input_files_size(); ++i) {
      if (lhs.input_files(i) != rhs.input_files(i)) {
        return false;
      }
    }
    return true;
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
  // task_log_mutex. readTaskLog runs its I/O without the mutex.
  size_t offset;
  std::string active_unit;
  Storage* storage = nullptr;
  // Time of the last rollForwardTaskLog. Only the compaction thread uses it.
  std::chrono::steady_clock::time_point last_rollforward_{};

  std::unordered_map<TaskRecord::TaskIdentifier, TaskStatus, TaskIdentifierHash, TaskIdentifierEqual> task_map;
  std::shared_mutex task_map_mutex;

  std::unordered_map<TaskRecord::TaskIdentifier, int, TaskIdentifierHash, TaskIdentifierEqual> dead_tasks;  // value: owner generation of the dead claim
  // Ticks of 100 ms without a heartbeat before a claim is dead. DB::DB sets
  // it from task_heartbeat_threshold_ticks; the default matches.
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

  // Replay every record appended since the last replay. Then mark dead
  // every task whose tick count reached the threshold. A replayed heartbeat
  // resets its task's count, so this is the only place dead tasks are found.
  // It must run periodically, not only when this writer attempts a claim.
  void rollForwardTaskLog();
  // rollForwardTaskLog, at most once per third of the threshold. The
  // compaction thread calls it on every pick. On Corfu a replay is two
  // fenced calls, so the replay is paced.
  void rollForwardTaskLogIfDue();

  // Ticks without a heartbeat after which a task is dead. Call before the
  // first rollForwardTaskLog.
  void setHeartbeatThreshold(int ticks) { this->task_heartbeat_threshold = ticks; }
  int heartbeatThreshold() const { return this->task_heartbeat_threshold; }

  bool isFirstWriterForTask(TaskRecord::TaskIdentifier* task_id, std::string const& client, int owner_generation);
  bool worthAppend(TaskRecord const& record);

  void appendToTaskLog(TaskRecord const& record);

  std::pair<TaskRecord::TaskIdentifier, int> getDeadTask();
};
}  // namespace ozonedb
#endif
