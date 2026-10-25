#include "compaction.h"
#include "db.h"
#include "helper.h"
namespace ozonedb {
Status CompactionWatcher::startCompactionWatcher(std::atomic<bool> const* active) {
  std::cout << "Starting compaction watcher" << std::endl;
  this->compaction_thread = new std::thread(&CompactionWatcher::watchForCompaction, this, active);
  return Status::kSuccess;
}
Status CompactionWatcher::stopCompactionWatcher() {
  if (this->compaction_thread == nullptr) return Status::kSuccess;  // compaction_enabled = false
  this->compaction_thread->join();
  return Status::kSuccess;
}

bool CompactionWatcher::shouldWorkOnTask(TaskRecord::TaskIdentifier* task_id, TaskRecord*& task_record, int owner_generation) {
  task_record = new TaskRecord();
  task_record->set_allocated_task_id(task_id);
  task_record->set_owner(this->fingerprint);
  task_record->set_status(TaskRecord::TASK_BEGIN);
  task_record->set_owner_generation(owner_generation);
  if (this->mode == Mode::Singleton) {
    return true;
  }
  bool worth_append = this->task_log_handler->worthAppend(*task_record);
  if (!worth_append) {
    return false;
  }
  this->task_log_handler->appendToTaskLog(*task_record);

  this->task_log_handler->rollForwardTaskLog();
  return this->task_log_handler->isFirstWriterForTask(task_id, this->fingerprint, owner_generation);
}

Status CompactionWatcher::watchForCompaction(std::atomic<bool> const* active) {
  while (*active) {
    // Task log protocol:
    //  1. Replay the task log, at most once per threshold/3 ticks. Claim any
    //     dead task at owner_generation + 1.
    //  2. Otherwise pick a task from the View.
    //  3. Append a TASK_BEGIN to the task log.
    //  4. Replay the task log. The first TASK_BEGIN for the task wins.
    //  5. The winner runs the task and heartbeats every 100 ms.
    //  6. A loser returns to 2.

    // A task with no heartbeat for task_heartbeat_threshold_ticks ticks
    // (default 30, 3 s) is dead.
    bool has_worked_on_compaction;
    Compaction* compaction = nullptr;
    TaskRecord* task_record = nullptr;
    
    Status status = pickCompaction(compaction, task_record, has_worked_on_compaction);
    
    if (!has_worked_on_compaction) {
      
      std::this_thread::sleep_for(std::chrono::milliseconds(100)); // only sleep when there's nothing to do
      
    } else {
      
      Status status = startCompaction(compaction, task_record);
      delete compaction;
      delete task_record;
      compaction = nullptr;
      task_record = nullptr;
      
      if (status != Status::kSuccess) {
        
        return Status::kFailure;  // crash
      }
    }
  }
  return Status::kSuccess;
}

Status CompactionWatcher::taskHeartbeat(Compaction* compaction, TaskRecord* task_record) {
  task_record->set_status(TaskRecord::TASK_IN_PROGRESS);
  while (!(compaction->finished) && !compaction->aborted.load()) {
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    this->task_log_handler->appendToTaskLog(*task_record);
  }
  // An abandoned task gets no TASK_COMPLETE. The claim lapses and a peer
  // reclaims the task (paper §4.4).
  if (compaction->aborted.load()) {
    return Status::kSuccess;
  }
  task_record->set_status(TaskRecord::TASK_COMPLETE);
  this->task_log_handler->appendToTaskLog(*task_record);
  return Status::kSuccess;
}

Status CompactionWatcher::pickCompaction(Compaction*& compaction, TaskRecord*& task_record, bool& has_worked_on_compaction) {
  this->metadata_handler->getLatestView(this->latest_view);

  if (this->mode != Mode::Singleton) {
    // Replay the task log on a timer, not only on a claim. The replay is
    // the only place dead tasks are found, so a writer that claims nothing
    // must still run it.
    this->task_log_handler->rollForwardTaskLogIfDue();

    // Dead tasks are claimed first, at owner_generation + 1, before any new
    // work. The new-work pickers below always find something under a
    // continuous write load, so a check after them would never run.
    auto result = this->task_log_handler->getDeadTask();
    auto dead_task = result.first;
    int owner_generation = result.second;
    while (dead_task.IsInitialized()) {
      auto* dead_task_id = new TaskRecord::TaskIdentifier(dead_task);
      std::vector<std::string> inputs(dead_task_id->input_files().begin(), dead_task_id->input_files().end());
      std::cout << getpid() << ":Found dead task " << formatTaskId(inputs, dead_task_id->destinationlevel(), !dead_task_id->compactintonextlevel())
                << " gen " << owner_generation << ", claiming gen " << owner_generation + 1 << std::endl;
      bool is_first_writer = shouldWorkOnTask(dead_task_id, task_record, owner_generation + 1);
      if (is_first_writer) {
        compaction = new Compaction();
        compaction->task_id = dead_task_id;
        has_worked_on_compaction = true;
        return Status::kSuccess;
      }
      delete task_record;
      task_record = nullptr;
      result = this->task_log_handler->getDeadTask();
      dead_task = result.first;
      owner_generation = result.second;
    }
  }

  // Log-level compaction: merge data log files into level 1.
  auto files = this->latest_view.getWithPrefix(metadata->log_prefix);
  while (files.size() >= 2) {
    compaction = new Compaction();
    compaction->task_id = new TaskRecord::TaskIdentifier();
    initInputUnit(compaction, files, this->metadata->compaction_input_file_number);
    if (compaction->task_id->input_files_size() < 2) {
      delete compaction;
      delete task_record;
      compaction = nullptr;
      task_record = nullptr;
      continue;
    }
    compaction->task_id->set_compactintonextlevel(true);
    compaction->task_id->set_destinationlevel(1);
    if (this->metadata->max_level == 1) {
      compaction->compaction_version = 0;
    }
    bool is_first_writer = shouldWorkOnTask(compaction->task_id, task_record, 0);
    if (is_first_writer) {
      has_worked_on_compaction = true;
      return Status::kSuccess;
    }
    delete compaction;
    delete task_record;
    compaction = nullptr;
    task_record = nullptr;
  };

  // SSTable-level compaction: a level over its size limit spills into the next.
  for (int level = 1; level < metadata->max_level; level++) {  // we don't need to compact the last level
    std::deque<std::string> level_files = this->latest_view.getWithPrefix(metadata->sstable_level_prefix + (std::to_string(level)));
    uint64_t file_size = 0;
    for (std::string const& file : level_files) {
      int each_file_size = this->latest_view.getFileSize(file);
      file_size += each_file_size;
    }
    while (file_size > metadata->level_size[level - 1]) {
      compaction = new Compaction();
      compaction->task_id = new TaskRecord::TaskIdentifier();
      initInputUnit(compaction, level_files, this->metadata->compaction_input_file_number);
      compaction->task_id->set_compactintonextlevel(true);
      compaction->task_id->set_destinationlevel(level + 1);
      bool is_first_writer = shouldWorkOnTask(compaction->task_id, task_record, 0);
      if (is_first_writer) {
        has_worked_on_compaction = true;
        return Status::kSuccess;
      }
      // Subtract the input sizes from the level size.
      for (int i = 0; i < compaction->task_id->input_files_size(); i++) {
        file_size -= this->latest_view.getFileSize(compaction->task_id->input_files(i));
      }
      delete compaction;
      delete task_record;
      compaction = nullptr;
      task_record = nullptr;
    }
  }

  // Last-level compaction: merge same-version files until the level holds
  // at most last_file_number_limit files.
  files = this->latest_view.getWithPrefix(metadata->sstable_level_prefix + std::to_string(metadata->max_level));
  while (files.size() > metadata->last_file_number_limit) {
    compaction = new Compaction();
    compaction->task_id = new TaskRecord::TaskIdentifier();
    Status init_status = initInputUnitInlastLayer(compaction, files, this->metadata->compaction_input_file_number);
    // initInputUnitInlastLayer returns kFailure when the level holds no run
    // of compaction_input_file_number same-version files. Leave the level
    // alone until the View changes. task_record is still null, so task_id
    // belongs to this function.
    if (init_status != Status::kSuccess || compaction->task_id->input_files_size() < 2) {
      delete compaction->task_id;
      delete compaction;
      compaction = nullptr;
      break;
    }
    compaction->task_id->set_compactintonextlevel(false);
    compaction->task_id->set_destinationlevel(this->metadata->max_level);
    // initInputUnitInlastLayer set compaction_version to chosen_version + 1:
    // the merge of two version-k files is one version-(k+1) file.
    bool is_first_writer = shouldWorkOnTask(compaction->task_id, task_record, 0);
    if (is_first_writer) {
      has_worked_on_compaction = true;
      return Status::kSuccess;
    }
    delete compaction;
    delete task_record;
    compaction = nullptr;
    task_record = nullptr;
  };
  has_worked_on_compaction = false;
  return Status::kSuccess;
}

Status CompactionWatcher::initInputUnit(Compaction* compaction, std::deque<std::string>& units, int limit) {
  int count = 0;
  while (count < limit && !units.empty()) {
    if (units.front() == this->latest_view.getCurrentLogTail()) {
      break;
    }
    compaction->task_id->add_input_files(units.front());
    units.pop_front();
    count++;
  }
  return Status::kSuccess;
}

Status CompactionWatcher::initInputUnitInlastLayer(Compaction* compaction, std::deque<std::string>& units, int limit) {
  int count = 0;
  std::vector<std::string> selected_files;  // candidate run of same-version files
  int chosen_version = getNumberBeforeUnderscore(units.front());
  int selected_index_start = 0;
  for (int i = 0; i < units.size() && count < limit; i++) {
    if (getNumberBeforeUnderscore(units[i]) == chosen_version) {
      selected_files.push_back(units[i]);
      count++;
    } else {
      selected_files.clear();
      selected_index_start = i;
      chosen_version = getNumberBeforeUnderscore(units[i]);
      count = 1;
      selected_files.push_back(units[i]);
    }
  }
  if (count < limit) {
    return Status::kFailure;
  } else {
    for (auto const& file : selected_files) {
      compaction->task_id->add_input_files(file);
    }
    units.erase(units.begin() + selected_index_start, units.begin() + selected_index_start + limit);
  }
  compaction->compaction_version = chosen_version + 1;
  return Status::kSuccess;
}

Status CompactionWatcher::startCompaction(Compaction* compaction, TaskRecord* task_record) {
  if (this->mode == Mode::Singleton) {
    return doCompactionWork(compaction);
  }
  std::thread t(&CompactionWatcher::taskHeartbeat, this, compaction, task_record);
  doCompactionWork(compaction);
  t.join();
  return Status::kSuccess;
}

Status CompactionWatcher::doCompactionWork(Compaction* compaction) {
  // Read every input, merge by key, write the outputs, publish the COMPACT,
  // then delete the inputs.
  bool log_level_compaction = false;
  std::string log_string = "Compacting ";
  std::unordered_map<std::string, std::shared_ptr<Record>> key_record;
  // Fault injection (compaction_abort_rate): give the task up before any
  // work, as if this writer had died with the claim held. Multi-process
  // mode only: in Singleton mode a failed task stops the watcher thread.
  if (this->metadata->compaction_abort_rate > 0.0 && this->mode != Mode::Singleton) {
    std::uniform_real_distribution<double> draw(0.0, 1.0);
    if (draw(abort_rng_) < this->metadata->compaction_abort_rate) {
      compaction->aborted.store(true);
      std::vector<std::string> inputs(compaction->task_id->input_files().begin(), compaction->task_id->input_files().end());
      std::cout << getpid() << ":COMPACT_ABORT " << formatTaskId(inputs, compaction->task_id->destinationlevel(), !compaction->task_id->compactintonextlevel()) << std::endl;
      return Status::kFailure;
    }
  }
  // The output version of a last-level merge is one above its inputs'.
  // Derive it from the task itself: a task reclaimed from a dead peer
  // arrives in a fresh Compaction whose version is still -1.
  if (!compaction->task_id->compactintonextlevel()) {
    compaction->compaction_version = getNumberBeforeUnderscore(compaction->task_id->input_files(0)) + 1;
  }
  if (compaction->task_id->input_files(0).find(this->metadata->log_prefix) != std::string::npos) {
    log_level_compaction = true;
    if (this->event_listener != nullptr) {
      this->event_listener->onLogCompactionStart();
    }
  } else {
    if (this->event_listener != nullptr) {
      this->event_listener->onSSTableCompactionStart();
    }
  }

  // Record accounting, printed with the "Compacting" line below.
  // records_in - unique is the number of overwritten keys. A skipped input
  // is a whole file whose records are not in the output.
  size_t records_in = 0;
  int inputs_skipped = 0;
  for (auto const& input : compaction->task_id->input_files()) {

    log_string += input + " ";
    if (input.find(this->metadata->log_prefix) != std::string::npos) {

      unsigned char* buffer = nullptr;
      size_t file_size = 0;
      std::shared_mutex& file_mutex = this->file_mutex_manager->getMutexForFile(input);
      std::shared_lock file_lock(file_mutex);

      Status read_status = this->storage->read(input, buffer, file_size);

      file_lock.unlock();
      // A peer's compaction can REMOVE an input log between the pick and
      // this read (same race as LRUCache::readDataLog). Skip the input;
      // the check after the loop completes the claim with no record.
      if (read_status != Status::kSuccess || buffer == nullptr) {
        delete[] buffer;
        ++inputs_skipped;
        std::cerr << "[compaction] input " << input << " unreadable (status="
                  << static_cast<int>(read_status) << "), skipped\n";
        continue;
      }
      std::vector<google::protobuf::Message*> messages;
      protobuf::deserializeMessages(buffer, file_size, messages, []() -> google::protobuf::Message* {
        return new Record();
      });
      delete[] buffer;
      buffer = nullptr;
      records_in += messages.size();
      for (auto* msg : messages) {
        // Take ownership of the parsed Record. A later record for the same
        // key replaces the entry.
        std::shared_ptr<Record> rec(static_cast<Record*>(msg));
        auto key = rec->key();
        key_record[std::move(key)] = std::move(rec);
      }
    } else {
      Table* table = nullptr;
      std::shared_mutex& file_mutex = this->file_mutex_manager->getMutexForFile(input);
      std::shared_lock file_lock(file_mutex);

      // One ranged read per compaction_read_bytes of the input, not one GET
      // per block.
      std::unordered_map<std::string, std::shared_ptr<Record>> records_tmp;
      Status read_status = Table::open(this->sstable_storage, input, table);
      if (read_status == Status::kSuccess && table == nullptr) read_status = Status::kFailure;
      if (read_status == Status::kSuccess) {
        read_status = table->getAll(records_tmp, this->metadata->compaction_read_bytes);
      }

      file_lock.unlock();
      delete table;
      table = nullptr;
      if (read_status != Status::kSuccess) {
        // An input that is gone was removed by a peer's compaction: skip it
        // (same race as the log branch above). An input that exists but is
        // unreadable must not be skipped: the COMPACT would then lack its
        // records and delete the file. Abandon the task; the claim lapses.
        if (!this->sstable_storage->exist(input)) {
          ++inputs_skipped;
          std::cerr << "[compaction] input " << input << " removed under us (status="
                    << static_cast<int>(read_status) << "), skipped\n";
          continue;
        }
        std::cerr << "[compaction] input " << input << " unreadable (status="
                  << static_cast<int>(read_status) << "), abandoning task; inputs kept\n";
        compaction->finished = false;
        compaction->aborted.store(true);
        return Status::kFailure;
      }
      if (records_tmp.empty()) {
        continue;  // nothing to merge, and begin() below would be end()
      }
      records_in += records_tmp.size();
      // Merge records_tmp into key_record; key_start and key_end are its key range.
      std::string key_start = records_tmp.begin()->first;
      std::string key_end = records_tmp.begin()->first;
      for (auto& record : records_tmp) {
        if (record.first < key_start) {
          key_start = record.first;
        }
        if (record.first > key_end) {
          key_end = record.first;
        }
        key_record[record.first] = std::move(record.second);
      }
      delete table;
    }
  }

  // An input was removed under this task: a peer ran a task that shares it,
  // and that peer's COMPACT already took the file out of the View. A record
  // whose first input is gone is parked by every replayer forever, so
  // complete the claim with no record: no outputs, no removals. The inputs
  // that remain stay in the View and go into a later task.
  if (inputs_skipped > 0) {
    std::cerr << "[compaction] " << inputs_skipped << " of " << compaction->task_id->input_files_size()
              << " inputs removed under us by a peer's compaction; completing the claim with no record\n";
    compaction->finished = true;
    return Status::kSuccess;
  }

  std::vector<std::shared_ptr<Record>> records;
  records.reserve(key_record.size());
  for (auto& [key, record] : key_record) {
    records.push_back(record);
  }

  // Sort by key and write the outputs, cut at the level's file size limit.

  log_string += "into";
  std::sort(records.begin(), records.end(),
            [](std::shared_ptr<Record> const& a, std::shared_ptr<Record> const& b) {
              return a->key() < b->key();
            });
  std::vector<std::string> output_files;
  std::vector<uint64_t> output_file_bytes;
  std::vector<std::pair<std::string, std::string>> key_ranges;
  std::pair<std::string, std::string> key_range;
  std::string dest_prefix = this->metadata->sstable_level_prefix + std::to_string(compaction->task_id->destinationlevel());
  if (!this->sstable_storage->exist(dest_prefix)) {
    this->sstable_storage->createDirectory(dest_prefix);
  }
  // Set when any output SSTable fails to write. A failed build abandons
  // the whole task and leaves the inputs alone.
  bool output_failed = false;
  // A last-level compaction emits one output: two version-k files become
  // one version-(k+1) file, so the level converges to last_file_number_limit
  // files. Every other level cuts its output at level_file_size_limit.
  bool const is_last_level = compaction->task_id->destinationlevel() == this->metadata->max_level;
  for (int i = 0; i < records.size(); i++) {
    if (compaction->outputBuilder == nullptr ||
        (!is_last_level && compaction->outputBuilder->fileSize() > this->metadata->level_file_size_limit[compaction->task_id->destinationlevel() - 1])) {
      if (compaction->outputBuilder != nullptr) {
        if (compaction->outputBuilder->finish() != Status::kSuccess) output_failed = true;
        key_range.second = records[i - 1]->key();
        key_ranges.push_back(key_range);
        output_file_bytes.push_back(compaction->outputBuilder->fileSize());
        delete compaction->outputBuilder;
        compaction->outputBuilder = nullptr;
      }
      auto now = std::chrono::system_clock::now();
      auto duration = now.time_since_epoch();
      auto nanoseconds = std::chrono::duration_cast<std::chrono::nanoseconds>(duration).count();
      std::string sstable_name = this->fingerprint + std::to_string(nanoseconds) + ".sst";
      std::string sstable_path = dest_prefix;
      if (compaction->compaction_version >= 0) {
        sstable_path.append("/").append(std::to_string(compaction->compaction_version)).append("_").append(sstable_name);
      } else
        sstable_path.append("/").append(sstable_name);
      output_files.push_back(sstable_path);
      log_string += ":" + sstable_path;
      compaction->outputBuilder = new TableBuilder(this->sstable_storage, sstable_path);
      compaction->outputBuilder->setLRUCache(this->lru_cache);
      key_range.first = records[i]->key();
    }
    compaction->outputBuilder->add(records[i]->key(), *records[i]);
  }
  
  if (compaction->outputBuilder != nullptr) {
    if (compaction->outputBuilder->finish() != Status::kSuccess) output_failed = true;
    key_range.second = records[records.size() - 1]->key();
    key_ranges.push_back(key_range);
    output_file_bytes.push_back(compaction->outputBuilder->fileSize());
    delete compaction->outputBuilder;
    compaction->outputBuilder = nullptr;
  }

  if (output_failed) {
    // Abandon the task without a record. The inputs stay, the claim lapses,
    // and a writer retries the compaction. A partial output is unreferenced;
    // the retry writes a new name.
    std::cerr << "[compaction] output write failed, abandoning task; inputs kept\n";
    compaction->finished = false;
    compaction->aborted.store(true);
    return Status::kFailure;
  }

  OperationRecord operation_record;
  operation_record.set_op_type(OperationRecord::COMPACT);
  for (auto const& input : compaction->task_id->input_files()) {
    operation_record.add_input_files(input);
  }
  for (int i = 0; i < key_ranges.size(); i++) {
    operation_record.add_key_start(key_ranges[i].first);
    operation_record.add_key_end(key_ranges[i].second);
    operation_record.add_output_file(output_files[i]);
    operation_record.add_output_bytes(static_cast<int64_t>(output_file_bytes[i]));
  }
  if (compaction->task_id->compactintonextlevel()) {
    operation_record.set_compact_in_last_level(false);
  } else {
    operation_record.set_compact_in_last_level(true);
  }
  this->metadata_handler->appendToMetadataLog(operation_record);

  // Apply the COMPACT to the local View before touching the inputs.
  // appendToMetadataLog only writes the record. Until the View lists the
  // outputs, a concurrent get could scan a removed input and miss a key.
  this->metadata_handler->rollForwardMetadataLog();

  // Sample sizes before the deletes: storage->size() reports 0 for a
  // removed file.
  std::vector<std::string> inputs;
  inputs.reserve(compaction->task_id->input_files_size());
  for (auto const& input : compaction->task_id->input_files()) {
    inputs.push_back(input);
  }
  // Remove through the backend that holds the file: log files live on
  // storage, SSTables on sstable_storage.
  Storage* input_storage = log_level_compaction ? this->storage : this->sstable_storage;
  int input_size = 0;
  for (auto const& input : inputs) {
    input_size += log_level_compaction ? static_cast<int>(input_storage->size(input))
                                       : static_cast<int>(this->latest_view.getFileSize(input));
  }

  // Drop LogKeyIndex entries and LRUCache log records for the inputs. Both
  // hold shared_ptr<Record>, so a reader that holds a Record keeps it alive.
  if (log_level_compaction && this->log_handler != nullptr) {
    this->log_handler->invalidateCompactedLog(inputs);
  }

  // Delete the inputs. This must run after the COMPACT record above is
  // durable, so replay sees the outputs before the inputs are gone.
  for (auto const& input : inputs) {
    input_storage->remove(input);
  }
  compaction->finished = true;
  std::cout << std::this_thread::get_id() << ":" << log_string
            << " [records_in=" << records_in << " unique=" << records.size()
            << " inputs_skipped=" << inputs_skipped
            << " outputs=" << output_files.size() << "]" << std::endl;
  // records and key_record release their shared_ptr<Record>s on return.
  // A concurrent reader that holds a Record keeps it alive.
  // input_size was sampled before the deletes above.
  if (this->event_listener != nullptr) {
    if (log_level_compaction) {
      this->event_listener->onLogCompactionCompletion(input_size);
    } else {
      int source_level = getSSTLayerNumber(compaction->task_id->input_files(0));
      this->event_listener->onSSTableCompactionCompletion(input_size, source_level);
    }
  }
  return Status::kSuccess;
}

}  // namespace ozonedb
