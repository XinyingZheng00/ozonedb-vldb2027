#include "metadata_log_handler.h"
#include "db.h"
#include "helper.h"
#include <algorithm>
#include <chrono>
#include <climits>
#include <iostream>
#include <unistd.h>
#include <vector>

namespace ozonedb {

namespace {
// Log `[COMPACTION_COMMITTED]` when a COMPACT record is applied to the local
// View. Every process logs it from its own replay, the compactor included.
// The line pairs with `[COMPACTION_NEEDED]` from task_log_handler.cpp by task_id.
inline void logCompactionCommitted(OperationRecord const* record) {
  // A COMPACT can carry no outputs when every input was empty. Its
  // destination is then unknown, so log it under the input level.
  int dest_level = record->output_file_size() > 0
                       ? getNumberInTheEnd(getPrefix(record->output_file(0)))
                       : getNumberInTheEnd(getPrefix(record->input_files(0)));
  std::vector<std::string> inputs(record->input_files().begin(),
                                  record->input_files().end());
  auto wall_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
                     std::chrono::system_clock::now().time_since_epoch())
                     .count();
  std::cerr << "[COMPACTION_COMMITTED] task_id="
            << formatTaskId(inputs, dest_level,
                            record->compact_in_last_level())
            << " wall_ns=" << wall_ns << " writer_pid=" << getpid()
            << std::endl;
}
}  // namespace

// All accessors use find(), so a probe for a missing prefix or file never
// mutates the maps. Views are shared behind a shared_ptr<View const>.
int View::getFileNumber(std::string const& prefix) const {
  auto it = storage_layout.find(prefix);
  return it == storage_layout.end() ? 0 : static_cast<int>(it->second.size());
}

std::deque<std::string> const& View::getWithPrefix(std::string const& prefix) const {
  static std::deque<std::string> const kEmpty;
  auto it = storage_layout.find(prefix);
  return it == storage_layout.end() ? kEmpty : it->second;
}

std::pair<std::string, std::string> View::getKeyRange(std::string const& file_name) const {
  auto it = key_range.find(file_name);
  return it == key_range.end()
             ? std::pair<std::string, std::string>{}
             : it->second;
}

size_t View::getFileSize(std::string const& file_name) const {
  auto it = file_size.find(file_name);
  return it == file_size.end() ? 0 : it->second;
}

// Append an OperationRecord to the metadata log.
void MetadataLogHandler::appendToMetadataLog(OperationRecord const& record) {
  int buffer_size;
  unsigned char* buffer = protobuf::serializeMessage(record, buffer_size);
  // No lock: active_unit never changes after the constructor, and the
  // storage layer orders concurrent appends itself. read_mutex here would
  // block every readMetadataLog across the Corfu round trip.
  this->storage->append(this->active_unit, buffer, buffer_size);
  delete[] buffer;
  buffer = nullptr;
}

Status MetadataLogHandler::startViewUpdate(std::atomic<bool> const* active) {
  this->update_view_thread = new std::thread(&MetadataLogHandler::rollForwardMetadataLogPeriodically, this, active);
  return Status::kSuccess;
}

Status MetadataLogHandler::stopViewUpdate() {
  this->update_view_thread->join();
  delete this->update_view_thread;
  this->update_view_thread = nullptr;
  return Status::kSuccess;
}

// Copies the View. Hot paths use latestViewSnapshot instead.
void MetadataLogHandler::getLatestView(View& view) {
  std::shared_lock<std::shared_mutex> lock(view_mutex);
  view = latest_view;
}

std::shared_ptr<View const> MetadataLogHandler::latestViewSnapshot() const {
  // Hot path from DB::get: keep it lock-free. The result is null before
  // the first publishSnapshotLocked; callers treat that as "no files yet".
  return std::atomic_load(&latest_snapshot_);
}

void MetadataLogHandler::publishSnapshotLocked() {
  // Caller holds view_mutex as a unique_lock. One copy per mutation;
  // each read is a refcount bump.
  auto snap = std::make_shared<View>(latest_view);
  std::atomic_store(&latest_snapshot_,
                    std::shared_ptr<View const>(std::move(snap)));
}

void MetadataLogHandler::markAppliedLocked(size_t start, size_t end) {
  if (end <= start) return;
  if (start <= applied_offset_) {
    if (end > applied_offset_) applied_offset_ = end;
  } else {
    // Applied ahead of the watermark: park until the hole below fills.
    size_t& parked_end = applied_gaps_[start];
    if (end > parked_end) parked_end = end;
  }
  for (auto it = applied_gaps_.begin();
       it != applied_gaps_.end() && it->first <= applied_offset_;) {
    if (it->second > applied_offset_) applied_offset_ = it->second;
    it = applied_gaps_.erase(it);
  }
}

// Read the OperationRecords appended since the last read.
//
// read_mutex is not held across storage->size() and storage->read(): on
// Corfu they fence the tailer and can stall. The offset is snapshotted
// under the lock and the I/O runs lock-free. The offset is then committed
// under the lock, only if nobody advanced it meanwhile. A loser drops its
// records; the caller catches up on the next pass.
std::vector<OperationRecord*> MetadataLogHandler::readMetadataLog(size_t* observed_size,
                                                                  size_t* batch_start) {
  if (observed_size != nullptr) *observed_size = 0;
  size_t local_offset;
  {
    std::unique_lock<std::shared_mutex> lock(read_mutex);
    local_offset = this->offset;
  }
  if (batch_start != nullptr) *batch_start = local_offset;
  if (!this->storage->exist(this->active_unit)) {
    return {};
  }
  size_t file_size = this->storage->size(this->active_unit);
  if (observed_size != nullptr) *observed_size = file_size;
  if (file_size == local_offset) {
    return {};
  }
  std::vector<google::protobuf::Message*> records;
  unsigned char* buffer = nullptr;
  Status read_status = this->storage->read(this->active_unit, buffer,
                                           local_offset, file_size - local_offset);
  if (read_status != Status::kSuccess || buffer == nullptr) {
    delete[] buffer;
    return {};
  }
  if (protobuf::deserializeMessages(buffer, file_size - local_offset, records, []() -> google::protobuf::Message* {
        return new OperationRecord();
      }) == Status::kFailure) {
    delete[] buffer;
    return {};
  }
  delete[] buffer;
  buffer = nullptr;
  {
    std::unique_lock<std::shared_mutex> lock(read_mutex);
    if (this->offset != local_offset) {
      // Another thread advanced the offset during the I/O. Its batch
      // covers this one; drop these records.
      for (auto const& r : records) {
        delete static_cast<OperationRecord*>(r);
      }
      return {};
    }
    this->offset = file_size;
  }
  std::vector<OperationRecord*> result;
  result.reserve(records.size());
  for (auto const& record : records) {
    result.push_back(static_cast<OperationRecord*>(record));
  }
  return result;
}

std::string MetadataLogHandler::rollforwardSingleOperationRecord(OperationRecord* record) {
  if (record->op_type() == OperationRecord::LOGCREATE) {
    std::string const& input_file = record->input_files()[0];
    std::string const& output_file = record->output_file()[0];
    std::string prefix = getPrefix(output_file);
    std::deque<std::string>& level_layout = this->latest_view.storage_layout[prefix];
    if (this->latest_view.current_log_tail.empty() || this->latest_view.current_log_tail == input_file) {
      level_layout.push_back(output_file);
      // The rolling writer ships the sealed tail size on the record
      // (log_handler.cpp, newTail), so no fenced storage->size() runs under
      // view_mutex. Records without the field fall back to 0.
      // Heuristic only: it is the emitter's unfenced size and feeds
      // compaction sizing. Readers must not use it as a read bound;
      // LRUCache::sealedLogSize fences for that.
      this->latest_view.file_size[input_file] =
          record->has_sealed_input_bytes() ? record->sealed_input_bytes() : 0;
      this->latest_view.current_log_tail = output_file;
      delete record;
      return "tail" + prefix;
    }
    if (getNumberInTheEnd(this->latest_view.current_log_tail) < getNumberInTheEnd(input_file)) {
      this->buffer["tail" + prefix].push({getNumberInTheEnd(input_file), record});
      return "buffered";
    }
    delete record;
    return "";
  } else if (record->op_type() == OperationRecord::COMPACT && !record->compact_in_last_level()) {
    std::string const& input_file = record->input_files()[0];
    std::string input_prefix = getPrefix(input_file);
    std::deque<std::string>& level_layout = this->latest_view.storage_layout[input_prefix];
    // A record whose first input does not head the level waits in the
    // buffer. An input that is not in the level at all (a duplicate record)
    // is parked at INT_MAX and never applied.
    if (level_layout.empty() || level_layout.front() != input_file) {
      auto it = std::find(level_layout.begin(), level_layout.end(), input_file);
      int index = (it == level_layout.end())
                      ? INT_MAX
                      : static_cast<int>(std::distance(level_layout.begin(), it));
      this->buffer[input_prefix].push({index, record});
      return "buffered";
    }
    for (auto const& input_file : record->input_files()) {
      // Remove the input from latest_view.
      latest_view.storage_layout[input_prefix].pop_front();
      if (latest_view.key_range.find(input_file) != latest_view.key_range.end()) {
        latest_view.key_range.erase(input_file);
      }
      if (latest_view.file_size.find(input_file) != latest_view.file_size.end()) {
        latest_view.file_size.erase(input_file);
      }
    }
    for (int i = 0; i < record->output_file_size(); i++) {
      std::string const& output_file = record->output_file(i);
      std::string output_prefix = getPrefix(output_file);
      latest_view.storage_layout[output_prefix].push_back(output_file);
      latest_view.key_range[output_file] = std::make_pair(record->key_start(i), record->key_end(i));
      // The compactor ships the output size on the record. Records without
      // output_bytes fall back to 0; the field feeds size heuristics only.
      latest_view.file_size[output_file] =
          (i < record->output_bytes_size()) ? record->output_bytes(i) : 0;
    }
    queueCacheEventLocked(record);
    logCompactionCommitted(record);
    delete record;
    return input_prefix;
  } else if (record->op_type() == OperationRecord::COMPACT && record->compact_in_last_level()) {
    std::string const& input_file = record->input_files()[0];
    std::string input_prefix = getPrefix(input_file);
    std::deque<std::string>& level_layout = this->latest_view.storage_layout[input_prefix];

    // Canonical replay order for last-level COMPACT records. The files of
    // one version are contiguous. The picker takes the first run of
    // same-version files from the front of the level. A record applies only
    // when its first input heads its version region; otherwise it waits in
    // the buffer until the records before it have applied. Every process
    // therefore applies the same records in the same order. All end with
    // the same deque, whatever their replay batching. A duplicate COMPACT
    // (KNOWN-ISSUES.md 1) has no inputs in the layout. It is parked at the
    // lowest priority and never applied.
    int input_version = getNumberBeforeUnderscore(input_file);
    size_t version_start = 0;
    while (version_start < level_layout.size() &&
           getNumberBeforeUnderscore(level_layout[version_start]) > input_version) {
      ++version_start;
    }
    if (version_start >= level_layout.size() || level_layout[version_start] != input_file) {
      auto it = std::find(level_layout.begin(), level_layout.end(), input_file);
      int index = (it == level_layout.end())
                      ? INT_MAX
                      : static_cast<int>(std::distance(level_layout.begin(), it));
      this->buffer[input_prefix].push({index, record});
      return "buffered";
    }

    int index = static_cast<int>(version_start);
    for (auto const& input : record->input_files()) {
      // The inputs sit at consecutive positions from `index`; each erase
      // shifts the next one into that position.
      level_layout.erase(level_layout.begin() + index);
      if (latest_view.key_range.find(input) != latest_view.key_range.end()) {
        latest_view.key_range.erase(input);
      }
      if (latest_view.file_size.find(input) != latest_view.file_size.end()) {
        latest_view.file_size.erase(input);
      }
    }
    for (int i = 0; i < record->output_file_size(); i++) {
      std::string const& output_file = record->output_file(i);
      // The outputs take the inputs' place, so the level stays in
      // version-then-age order.
      int insert_at = std::min(index + i, static_cast<int>(level_layout.size()));
      level_layout.insert(level_layout.begin() + insert_at, output_file);
      latest_view.key_range[output_file] = std::make_pair(record->key_start(i), record->key_end(i));
      latest_view.file_size[output_file] =
          (i < record->output_bytes_size()) ? record->output_bytes(i) : 0;
    }
    queueCacheEventLocked(record);
    logCompactionCommitted(record);
    delete record;
    return input_prefix;
  }
  return "";
}

// Apply every buffered record for `layer` that can apply now. Each pass
// takes the whole buffer and retries every record; one that still cannot
// apply re-buffers itself. Repeat until a pass applies nothing, so one
// parked record never blocks the layer.
// Caller holds view_mutex as a unique_lock.
void MetadataLogHandler::drainBuffer(std::string const& layer) {
  while (true) {
    auto& buf = this->buffer[layer];
    if (buf.empty()) return;
    std::vector<OperationRecord*> attempts;
    attempts.reserve(buf.size());
    while (!buf.empty()) {
      attempts.push_back(buf.top().second);
      buf.pop();
    }
    bool any_applied = false;
    for (auto* op : attempts) {
      if (rollforwardSingleOperationRecord(op) != "buffered") any_applied = true;
    }
    if (!any_applied) return;
  }
}

void MetadataLogHandler::queueCacheEventLocked(OperationRecord const* record) {
  if (this->lru_cache == nullptr || record->output_file_size() == 0) return;
  CompactionEvent ev;
  ev.inputs.assign(record->input_files().begin(), record->input_files().end());
  ev.outputs.assign(record->output_file().begin(), record->output_file().end());
  for (int i = 0; i < record->output_file_size(); ++i) {
    ev.output_bytes.push_back(i < record->output_bytes_size() ? static_cast<size_t>(record->output_bytes(i)) : 0);
  }
  ev.dest_level = getNumberInTheEnd(getPrefix(record->output_file(0)));
  pending_cache_events_.push_back(std::move(ev));
  has_pending_cache_events_.store(true, std::memory_order_release);
}

void MetadataLogHandler::drainCacheEvents() {
  if (!has_pending_cache_events_.load(std::memory_order_acquire)) return;
  std::vector<CompactionEvent> events;
  {
    std::unique_lock<std::shared_mutex> lock(view_mutex);
    events.swap(pending_cache_events_);
    has_pending_cache_events_.store(false, std::memory_order_release);
  }
  if (this->lru_cache == nullptr) return;
  for (auto const& ev : events) {
    // The View dropped the inputs already; now the cache does. A reader on
    // the old snapshot may still probe an input and see a transient miss.
    size_t cached_input_blocks = 0;
    bool log_inputs = !ev.inputs.empty();
    for (auto const& input : ev.inputs) {
      cached_input_blocks += this->lru_cache->invalidateSSTable(input);
      if (this->sstable_removed_listener_) this->sstable_removed_listener_(input);
      if (LRUCache::levelSlot(input) != 0) log_inputs = false;
    }
    // The warm worker decides whether to prefetch the outputs: enabled,
    // level, budget, and affinity (the blocks dropped, or the destination
    // level's lookups when the inputs are log files).
    this->lru_cache->onCompactionApplied(ev.outputs, ev.output_bytes, ev.dest_level, cached_input_blocks, log_inputs);
  }
}

// Background replay of the metadata log, every 100 ms. The tail-size
// refresh runs with no lock held (refreshTailSizeUnlocked). A Corfu fence
// therefore never stalls foreground reads and writes under view_mutex.
void MetadataLogHandler::rollForwardMetadataLogPeriodically(std::atomic<bool> const* active) {
  while (*active) {
    size_t observed = 0;
    size_t batch_start = 0;
    std::vector<OperationRecord*> records = readMetadataLog(&observed, &batch_start);
    if (records.empty()) {
      refreshTailSizeUnlocked();
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      continue;
    }
    {
      std::unique_lock<std::shared_mutex> lock(view_mutex);
      for (auto const& record : records) {
        std::string modified_layer = rollforwardSingleOperationRecord(record);
        if (modified_layer != "buffered") drainBuffer(modified_layer);
      }
      markAppliedLocked(batch_start, observed);
      publishSnapshotLocked();
    }
    drainCacheEvents();  // outside the view_mutex scope
    refreshTailSizeUnlocked();
    if (this->event_listener != nullptr)
      this->event_listener->onViewUpdate();
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
}

// Refresh latest_view.tail_size and file_size[current_log_tail]. The
// storage->size() call can fence the Corfu tailer, so it runs with no
// lock held.
void MetadataLogHandler::refreshTailSizeUnlocked() {
  std::string tail_name;
  {
    std::shared_lock<std::shared_mutex> lock(view_mutex);
    tail_name = this->latest_view.current_log_tail;
  }
  if (tail_name.empty()) return;
  size_t tail_size = this->storage->size(tail_name);
  std::unique_lock<std::shared_mutex> lock(view_mutex);
  // Apply only if the tail has not rolled since the sample.
  if (tail_name == this->latest_view.current_log_tail) {
    this->latest_view.tail_size = tail_size;
    this->latest_view.file_size[tail_name] = tail_size;
    publishSnapshotLocked();
  }
}

// Fence and synchronous replay, no View copy. storage->size() stays out of
// unique_lock<view_mutex>. See the header for the linearizable_reads role.
size_t MetadataLogHandler::syncView() {
  // `target` is the log length the first readMetadataLog observed. On Corfu
  // that size() is fenced, so it covers every operation sequenced before
  // the caller's fence point. The loop handles the offset race: a loser's
  // batch is discarded while the winner may have applied less than target.
  // The pass cap is a livelock backstop for a read that keeps failing.
  size_t target = 0;
  bool have_target = false;
  constexpr int kMaxPasses = 100;
  for (int pass = 0; pass < kMaxPasses; ++pass) {
    size_t observed = 0;
    size_t batch_start = 0;
    std::vector<OperationRecord*> records = readMetadataLog(&observed, &batch_start);
    if (!have_target) {
      target = observed;
      have_target = true;
    }
    bool reached = false;
    size_t applied = 0;
    {
      std::unique_lock<std::shared_mutex> lock(view_mutex);
      for (auto const& record : records) {
        std::string modified_layer = rollforwardSingleOperationRecord(record);
        if (modified_layer != "buffered") drainBuffer(modified_layer);
      }
      if (!records.empty()) {
        markAppliedLocked(batch_start, observed);
      }
      // Publish only when the View changed or no snapshot exists yet
      // (openDB's first replay needs a non-null snapshot). A publish copies
      // the View, and strict reads call syncView once per get.
      if (!records.empty() || std::atomic_load(&latest_snapshot_) == nullptr) {
        publishSnapshotLocked();
      }
      // Gate on applied_offset_, not `offset`: readMetadataLog commits the
      // offset before its caller applies the batch. The loop re-checks
      // until a racing winner has applied.
      if (applied_offset_ >= target) {
        reached = true;
        applied = applied_offset_;
      }
    }
    // Cache work for the COMPACTs this pass applied: after the lock, before
    // the caller sees the new View.
    drainCacheEvents();
    if (reached) return applied;
  }
  {
    std::shared_lock<std::shared_mutex> lock(view_mutex);
    std::cerr << "[metadata_log] syncView: view never reached fence target "
              << target << " (applied " << applied_offset_ << ") after "
              << kMaxPasses << " passes\n";
    return applied_offset_;
  }
}

// Synchronous replay that returns a copy of the View with a fresh tail
// size. The log-roll and put paths use it; strict reads use syncView().
View MetadataLogHandler::rollForwardMetadataLog() {
  syncView();
  refreshTailSizeUnlocked();
  std::shared_lock<std::shared_mutex> lock(view_mutex);
  return latest_view;
}
void MetadataLogHandler::initSSTMetadata() {
  // Runs only at open, before any other thread exists, so no lock.
  for (auto const& entry : this->latest_view.storage_layout) {
    if (entry.first.find("sstable") == std::string::npos) {
      continue;
    } else {
      for (auto it = entry.second.cbegin(); it != entry.second.cend(); it++) {
        Table* table = nullptr;
        this->lru_cache->getSSTable(*it, table);
      }
    }
  }
}
}  // namespace ozonedb
