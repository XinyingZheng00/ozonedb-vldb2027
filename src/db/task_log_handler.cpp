#include "task_log_handler.h"
#include "helper.h"
#include "protobuf_serializer.h"
#include <algorithm>
#include <chrono>
#include <iostream>
#include <unistd.h>

namespace ozonedb {
//========================================read and write=======================================
void TaskLogHandler::appendToTaskLog(TaskRecord const& record) {
  int buffer_size;
  unsigned char* buffer = protobuf::serializeMessage(record, buffer_size);
  {
    std::unique_lock<std::shared_mutex> lock(task_log_mutex);
    this->storage->append(this->active_unit, buffer, buffer_size);
  }
  delete[] buffer;
  buffer = nullptr;
  // [COMPACTION_NEEDED] is logged only for a new task: TASK_BEGIN at
  // owner_generation 0. Heartbeats, completions and takeovers are excluded.
  // The log line runs outside task_log_mutex, which heartbeat threads contend on.
  if (record.status() == TaskRecord::TASK_BEGIN &&
      record.owner_generation() == 0) {
    auto wall_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
                       std::chrono::system_clock::now().time_since_epoch())
                       .count();
    std::vector<std::string> inputs(record.task_id().input_files().begin(),
                                    record.task_id().input_files().end());
    std::cerr << "[COMPACTION_NEEDED] task_id="
              << formatTaskId(inputs,
                              record.task_id().destinationlevel(),
                              !record.task_id().compactintonextlevel())
              << " wall_ns=" << wall_ns << " writer_pid=" << getpid()
              << std::endl;
  }
}

// Reads the records appended since the last read.
//
// The fenced exist() and size() calls run without task_log_mutex.
// appendToTaskLog takes that mutex for every heartbeat, so a stall there
// would silence this writer's own heartbeats. The offset is snapshotted
// under the mutex and the I/O runs lock-free. The offset is then committed
// under the mutex, only if nobody advanced it meanwhile. A loser drops its
// records; the caller catches up on its next read. The offset moves only
// after a successful parse, so a failed read retries from the same place.
Status TaskLogHandler::readTaskLog(std::vector<TaskRecord*>& result) {
  size_t local_offset;
  {
    std::shared_lock<std::shared_mutex> lock(task_log_mutex);
    local_offset = this->offset;
  }
  if (!this->storage->exist(this->active_unit)) {
    return Status::kSuccess;
  }
  size_t file_size = this->storage->size(this->active_unit);
  if (file_size <= local_offset) {
    return Status::kSuccess;
  }
  size_t size = file_size - local_offset;
  unsigned char* buffer = nullptr;
  Status read_status =
      this->storage->read(this->active_unit, buffer, local_offset, size);
  if (read_status != Status::kSuccess || buffer == nullptr) {
    delete[] buffer;
    return Status::kFailure;
  }
  std::vector<google::protobuf::Message*> records;
  if (protobuf::deserializeMessages(buffer, size, records, []() -> google::protobuf::Message* {
        return new TaskRecord();
      }) == Status::kFailure) {
    delete[] buffer;
    return Status::kFailure;
  }
  delete[] buffer;
  buffer = nullptr;
  {
    std::unique_lock<std::shared_mutex> lock(task_log_mutex);
    if (this->offset != local_offset) {
      // Another reader advanced the offset during the I/O. Its batch
      // covers this one.
      for (auto* record : records) {
        delete record;
      }
      return Status::kSuccess;
    }
    this->offset = file_size;
  }
  result.reserve(result.size() + records.size());
  for (auto const& record : records) {
    result.push_back(static_cast<TaskRecord*>(record));
  }
  return Status::kSuccess;
}

bool TaskLogHandler::worthAppend(TaskRecord const& record) {
  if (record.status() == TaskRecord::TASK_BEGIN &&
      (task_map.find(record.task_id()) == task_map.end() || task_map[record.task_id()].owner_generation < record.owner_generation())) {
    return true;
  }
  return false;
}

void TaskLogHandler::rollforwardSingleTask(TaskRecord const& task_record) {
  std::unique_lock<std::shared_mutex> task_map_lock(task_map_mutex);
  if (task_record.status() == TaskRecord::TASK_BEGIN &&
      (task_map.find(task_record.task_id()) == task_map.end() ||
       task_map[task_record.task_id()].owner_generation < task_record.owner_generation())) {
    task_map[task_record.task_id()].owner = task_record.owner();
    task_map[task_record.task_id()].owner_generation = task_record.owner_generation();
    task_map[task_record.task_id()].status = TaskRecord::TASK_BEGIN;
    task_map[task_record.task_id()].heartbeat = 0;
    std::unique_lock<std::shared_mutex> lock(dead_tasks_mutex);
    dead_tasks.erase(task_record.task_id());
    lock.unlock();
  }
  // Same owner and generation: reset the tick count or record completion.
  if (task_map[task_record.task_id()].owner == task_record.owner() && task_map[task_record.task_id()].owner_generation == task_record.owner_generation()) {
    if (task_record.status() == TaskRecord::TASK_IN_PROGRESS) {
      task_map[task_record.task_id()].heartbeat = 0;
    } else if (task_record.status() == TaskRecord::TASK_COMPLETE) {
      task_map[task_record.task_id()].status = TaskRecord::TASK_COMPLETE;
    }
    std::unique_lock<std::shared_mutex> lock(dead_tasks_mutex);
    dead_tasks.erase(task_record.task_id());
    lock.unlock();
  }
}

void TaskLogHandler::rollForwardTaskLog() {
  std::vector<TaskRecord*> records;
  while (readTaskLog(records) != Status::kSuccess) {
    for (auto* record : records) {
      delete record;
    }
    records.clear();
  }
  if (!records.empty()) {
    for (auto const& record : records) {
      rollforwardSingleTask(*record);
    }
  }

  std::unique_lock<std::shared_mutex> task_map_lock(task_map_mutex);
  for (auto it = task_map.begin(); it != task_map.end(); it++) {
    if (it->second.heartbeat >= task_heartbeat_threshold && it->second.status != TaskRecord::TASK_COMPLETE && it->second.status != TaskRecord::TASK_DEAD) {
      std::vector<std::string> inputs(it->first.input_files().begin(), it->first.input_files().end());
      std::cout << getpid() << ":Task " << formatTaskId(inputs, it->first.destinationlevel(), !it->first.compactintonextlevel())
                << " owned by " << it->second.owner << " gen " << it->second.owner_generation
                << " is dead (hb=" << it->second.heartbeat << ")" << std::endl;
      std::unique_lock<std::shared_mutex> lock(dead_tasks_mutex);
      dead_tasks[it->first] = it->second.owner_generation;
      lock.unlock();
      it->second.status = TaskRecord::TASK_DEAD;
    }
  }
  task_map_lock.unlock();
  last_rollforward_ = std::chrono::steady_clock::now();

  for (auto* record : records) {
    delete record;
  }
}

void TaskLogHandler::rollForwardTaskLogIfDue() {
  int interval_ticks = std::max(1, task_heartbeat_threshold / 3);
  auto interval = std::chrono::milliseconds(100) * interval_ticks;
  if (std::chrono::steady_clock::now() - last_rollforward_ < interval) {
    return;
  }
  rollForwardTaskLog();
}

bool TaskLogHandler::isFirstWriterForTask(TaskRecord::TaskIdentifier* task_id, std::string const& client, int owner_generation) {
  std::unique_lock<std::shared_mutex> task_map_lock(task_map_mutex);
  if (task_map.find(*task_id) == task_map.end()) {
    return false;
  }
  if (task_map[*task_id].owner == client && task_map[*task_id].status == TaskRecord::TASK_BEGIN && task_map[*task_id].owner_generation == owner_generation) {
    task_map[*task_id].status = TaskRecord::TASK_IN_PROGRESS;
    return true;
  }
  return false;
}

std::pair<TaskRecord::TaskIdentifier, int> TaskLogHandler::getDeadTask() {
  std::unique_lock<std::shared_mutex> dead_tasks_lock(dead_tasks_mutex);
  if (dead_tasks.empty()) {
    return std::pair(TaskRecord::TaskIdentifier(), -1);
  }
  TaskRecord::TaskIdentifier task_id = dead_tasks.begin()->first;
  int owner_generation = dead_tasks.begin()->second;
  dead_tasks.erase(dead_tasks.begin());
  return std::pair(task_id, owner_generation);
}

// =======================================[heartbeats]=======================================
void TaskLogHandler::updateHeartbeats() {
  while (!stop_thread) {
    std::unique_lock<std::shared_mutex> task_map_lock(task_map_mutex);
    for (auto& task : task_map) {
      task.second.heartbeat++;
    }
    task_map_lock.unlock();

    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
}

void TaskLogHandler::startHeartbeatThread() {
  this->stop_thread = false;
  this->heartbeat_thread = std::thread(&TaskLogHandler::updateHeartbeats, this);
}

void TaskLogHandler::stopHeartbeatThread() {
  this->stop_thread = true;
  if (this->heartbeat_thread.joinable()) {
    this->heartbeat_thread.join();
  }
}

}  // namespace ozonedb