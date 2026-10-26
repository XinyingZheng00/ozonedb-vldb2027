#ifndef METADATA_H
#define METADATA_H

#include "protobuf/record.pb.h"
#include "read_json.h"
#include <string>
#include <unordered_map>
#include <vector>
namespace ozonedb {

// The only policy: every process runs a CompactionWatcher that claims
// tasks through the task log (compaction_policy = 0).
enum class CompactionPolicy {
  kHoAl  // Each process picks tasks and runs them at once.
};

enum class BackendKind {
  kLocal,
  kCorfu,
  kS3
};

class Metadata {
 public:
  /**
   * @brief Paths and backends.
   */
  std::string DBpath;
  BackendKind backend_kind = BackendKind::kLocal;
  // Corfu backend settings.
  std::string corfu_endpoint;
  std::string corfu_stream_name = "ozonedb";
  // true: ack an append on the sequencer's answer (fast path).
  // false or 0: every append waits for the tailer.
  bool corfu_fast_ack = true;
  // Write mode. sync (true, the default): a put is acked once its bytes
  // are sequenced in the log, so an acked put is durable. async (false):
  // a put is acked from the local buffer, which is submitted every
  // corfu_commit_interval_ms (must be > 0 then), so an ack precedes
  // durability by up to one interval. Async is the "OzoneKV-async"
  // series of the paper; it cannot be combined with linearizable_reads.
  bool corfu_sync_writes = true;
  int corfu_commit_interval_ms = 0;
  // Corfu client tuning.
  int corfu_read_batch = 1000;             // addresses per ReadLogRequest
  int corfu_idle_poll_ms = 5;              // wait between stream-tail queries
  int corfu_hole_fill_timeout_ms = 10000;  // EMPTY address -> HOLE write
  int corfu_request_timeout_ms = 5000;     // per RPC

  // Optional separate backend for SSTables (paper §3.5). Unset: SSTables
  // share the log backend.
  bool sstable_backend_set = false;
  BackendKind sstable_backend_kind = BackendKind::kLocal;
  std::string sstable_dir;  // key prefix inside the S3 bucket
  // S3-compatible store settings, used when sstable_backend_kind == kS3.
  // Empty endpoint: the AWS endpoint.
  std::string s3_endpoint;
  std::string s3_region = "us-east-1";
  std::string s3_bucket;
  std::string s3_access_key;
  std::string s3_secret_key;
  bool s3_use_path_style = true;  // MinIO default. Set false for AWS S3.
  std::string metadata_log = "metadata.log";
  std::string task_log = "task.log";
  std::string log_prefix = "datalog";
  std::string sstable_level_prefix = "sstable";
  int mode = -1;

  /**
   * @brief Size limits.
   */
  uint64_t log_file_size_limit;
  int max_level;
  uint64_t base_file_number_limit;
  uint64_t last_file_number_limit = 4;
  std::vector<uint64_t> level_size;
  std::vector<uint64_t> level_file_size_limit;

  CompactionPolicy compaction_policy;

  // LRU cache capacity in bytes for log records and SSTable blocks.
  // Default 32 MB.
  uint64_t lru_cache_bytes = 33554432;
  // Largest ranged read compaction issues for one SSTable input
  // (Table::getAll). Default equals level_file_size_limit, so one input is
  // one read. Lower it to bound memory per compaction.
  uint64_t compaction_read_bytes = 67108864;

  // Cache warming. When a COMPACT is applied to this process's View, the
  // outputs are read once and put in the block cache. Off by default.
  // Each rule below skips an output and counts the skip in the
  // "[lru_cache] levels" line.
  bool cache_warm_enabled = false;
  // Warm outputs whose level is at most this.
  int cache_warm_max_level = 1;
  // Warm an output only if its size is at most this fraction of
  // lru_cache_bytes.
  double cache_warm_max_fraction = 0.25;
  // Warm only if this process held at least this many cached blocks of
  // the compaction's inputs.
  uint64_t cache_warm_min_input_blocks = 1;

  // Disk tier for SSTables. Empty dir: off. The dir gets a trailing '/'.
  // Requires sstable_backend and disk_cache_bytes > 0.
  std::string disk_cache_dir;
  uint64_t disk_cache_bytes = 0;
  uint64_t disk_cache_chunk_bytes = 67108864;
  bool disk_cache_drop_pages = true;
  uint64_t disk_cache_fill_queue = 256;
  // "file" keeps whole SSTables. "chunk" keeps disk_cache_entry_bytes pieces.
  // "frequency" is TinyLFU admission. "always" is evict-to-fit.
  std::string disk_cache_mode = "chunk";
  uint64_t disk_cache_entry_bytes = 65536;
  std::string disk_cache_admission = "frequency";
  uint64_t disk_cache_admit_window = 0;

  // true: log reads trust the key index and skip the log-file scan.
  // addRecord and, on Corfu, the remote-append listener keep the index
  // current. An index miss is a log miss. The DB still reads SSTables.
  bool trust_background_tail = false;

  // true: every DB::get takes one fence (Storage::SyncScope), rolls the
  // View forward, and skips the unfenced key-index probe. Every later
  // storage call in the get reuses the thread-local fence token. A get
  // then sees every put acked before it started, on any writer.
  // Mutually exclusive with trust_background_tail: the constructor throws
  // on both. Assumes the write side keeps its sync defaults
  // (CorfuDBStorage::sync_mode_).
  bool linearizable_reads = false;

  // Log trimming. true: this process runs a LogTrimmer. Every
  // log_trim_interval_ms it writes a checkpoint of the live log state to
  // sstable_storage under <sstable_dir>/<checkpoint_dir>/ and trims the
  // Corfu stream behind the previous checkpoint. One trimmer per cluster
  // is enough. Every process loads the newest checkpoint at open.
  // Requires backend = corfu and a separate sstable_backend.
  bool log_trim_enabled = false;
  uint64_t log_trim_interval_ms = 30000;
  uint64_t log_trim_min_entries = 100000;
  int log_trim_keep_checkpoints = 2;
  std::string checkpoint_dir = "checkpoint";

  // Dead-task takeover (paper section 4.4). A task owner appends a
  // heartbeat every 100 ms. A task with this many ticks since its last
  // replayed heartbeat is dead, and any writer may claim it at the next
  // owner_generation. 30 ticks is 3 s. The task log is replayed at most
  // every third of this. On Corfu set it above
  // corfu_hole_fill_timeout_ms / 100, because a hole stalls the tailer.
  int task_heartbeat_threshold_ticks = 30;

  // false: start no compaction watcher. The process only appends and reads.
  bool compaction_enabled = true;

  // Fault injection (paper section 6). With this probability a claimed
  // compaction is abandoned before any work, as if the writer had died.
  // The claim gets no TASK_COMPLETE, so peers reclaim it after
  // task_heartbeat_threshold_ticks. 0 is off. Not a tuning knob.
  double compaction_abort_rate = 0.0;

  /**
   * @brief Local settings.
   */
  int compaction_input_file_number = 2;  // files per compaction

  /**
   * @brief Read the config file into this object.
   *
   * @param shared_config_path
   */
  Metadata(std::string const& shared_config_path) {
    std::map<std::string, std::string> result = parseJSON(shared_config_path);
    DBpath = result["db_path"];
    log_file_size_limit = std::stol(result["log_file_size_limit"]);
    mode = std::stoi(result["mode"]);

    auto backend_it = result.find("backend");
    if (backend_it != result.end()) {
      std::string const& b = backend_it->second;
      if (b == "local") {
        backend_kind = BackendKind::kLocal;
      } else if (b == "corfu") {
        backend_kind = BackendKind::kCorfu;
      } else {
        throw std::runtime_error("Unknown backend in config: " + b);
      }
    }

    if (backend_kind == BackendKind::kCorfu) {
      corfu_endpoint = result["corfu_endpoint"];
      auto stream_it = result.find("corfu_stream_name");
      if (stream_it != result.end()) corfu_stream_name = stream_it->second;
      auto fast_it = result.find("corfu_fast_ack");
      if (fast_it != result.end()) {
        corfu_fast_ack = !(fast_it->second == "false" || fast_it->second == "0");
      }
      if (auto it = result.find("corfu_sync_writes"); it != result.end()) {
        corfu_sync_writes = !(it->second == "false" || it->second == "0");
      }
      if (auto it = result.find("corfu_commit_interval_ms"); it != result.end()) {
        corfu_commit_interval_ms = std::stoi(it->second);
        if (corfu_commit_interval_ms < 0) throw std::runtime_error("config error: corfu_commit_interval_ms must be >= 0");
      }
      if (!corfu_sync_writes && corfu_commit_interval_ms <= 0) {
        throw std::runtime_error(
            "config error: corfu_sync_writes=false needs corfu_commit_interval_ms > 0");
      }
      if (auto it = result.find("corfu_read_batch"); it != result.end()) {
        corfu_read_batch = std::stoi(it->second);
        if (corfu_read_batch < 1) throw std::runtime_error("config error: corfu_read_batch must be >= 1");
      }
      if (auto it = result.find("corfu_idle_poll_ms"); it != result.end()) {
        corfu_idle_poll_ms = std::stoi(it->second);
      }
      if (auto it = result.find("corfu_hole_fill_timeout_ms"); it != result.end()) {
        corfu_hole_fill_timeout_ms = std::stoi(it->second);
      }
      if (auto it = result.find("corfu_request_timeout_ms"); it != result.end()) {
        corfu_request_timeout_ms = std::stoi(it->second);
      }
    }

    // Optional SSTable backend (paper §3.5). Absent: share the log backend.
    auto sb_it = result.find("sstable_backend");
    if (sb_it != result.end()) {
      sstable_backend_set = true;
      std::string const& sb = sb_it->second;
      if (sb == "local") {
        sstable_backend_kind = BackendKind::kLocal;
      } else if (sb == "s3") {
        sstable_backend_kind = BackendKind::kS3;
        s3_endpoint   = result.count("s3_endpoint")   ? result["s3_endpoint"]   : "";
        s3_region     = result.count("s3_region")     ? result["s3_region"]     : "us-east-1";
        s3_bucket     = result["s3_bucket"];
        s3_access_key = result.count("s3_access_key") ? result["s3_access_key"] : "";
        s3_secret_key = result.count("s3_secret_key") ? result["s3_secret_key"] : "";
        s3_use_path_style = result.count("s3_use_path_style")
                                ? result["s3_use_path_style"] == "true"
                                : true;
      } else {
        throw std::runtime_error("Unknown sstable_backend in config: " + sb);
      }
      sstable_dir = result.count("sstable_dir") ? result["sstable_dir"] : "";
    }

    std::string level_size_str = result["level_size"];
    std::vector<std::string> level_size_ = jsonArrayToVector(level_size_str);
    for (auto const& size : level_size_) {
      level_size.push_back(std::stol(size));
    }
    std::string level_file_size_limit_str = result["level_file_size_limit"];
    std::vector<std::string> level_file_size_limit_ = jsonArrayToVector(level_file_size_limit_str);
    for (auto const& size : level_file_size_limit_) {
      level_file_size_limit.push_back(std::stol(size));
    }

    max_level = std::stoi(result["max_level"]);

    assert(max_level >= 2);  // The output-unit logic needs two levels.
    base_file_number_limit = std::stoi(result["base_file_number_limit"]);
    if (std::stoi(result["compaction_policy"]) != 0) {
      throw std::runtime_error("config error: compaction_policy must be 0 (the task-log policy); the experimental policies were removed");
    }
    compaction_policy = CompactionPolicy::kHoAl;

    auto lru_it = result.find("lru_cache_bytes");
    if (lru_it != result.end()) {
      lru_cache_bytes = std::stoull(lru_it->second);
    }

    if (auto it = result.find("task_heartbeat_threshold_ticks"); it != result.end()) {
      task_heartbeat_threshold_ticks = std::stoi(it->second);
      if (task_heartbeat_threshold_ticks < 1) {
        throw std::runtime_error("config error: task_heartbeat_threshold_ticks must be >= 1");
      }
    }
    if (auto it = result.find("compaction_abort_rate"); it != result.end()) {
      compaction_abort_rate = std::stod(it->second);
      if (compaction_abort_rate < 0.0 || compaction_abort_rate > 1.0) {
        throw std::runtime_error("config error: compaction_abort_rate must be in [0, 1]");
      }
    }
    if (auto it = result.find("compaction_enabled"); it != result.end()) {
      compaction_enabled = !(it->second == "false" || it->second == "0");
    }

    if (auto it = result.find("compaction_read_bytes"); it != result.end()) {
      compaction_read_bytes = std::stoull(it->second);
    }
    if (auto it = result.find("cache_warm_enabled"); it != result.end()) {
      cache_warm_enabled = (it->second == "true" || it->second == "1");
    }
    if (auto it = result.find("cache_warm_max_level"); it != result.end()) {
      cache_warm_max_level = std::stoi(it->second);
    }
    if (auto it = result.find("cache_warm_max_fraction"); it != result.end()) {
      cache_warm_max_fraction = std::stod(it->second);
    }
    if (auto it = result.find("cache_warm_min_input_blocks"); it != result.end()) {
      cache_warm_min_input_blocks = std::stoull(it->second);
    }

    if (auto it = result.find("disk_cache_dir"); it != result.end() && !it->second.empty()) {
      disk_cache_dir = it->second;
      if (disk_cache_dir.back() != '/') disk_cache_dir += '/';
    }
    if (auto it = result.find("disk_cache_bytes"); it != result.end()) {
      disk_cache_bytes = std::stoull(it->second);
    }
    if (auto it = result.find("disk_cache_chunk_bytes"); it != result.end()) {
      disk_cache_chunk_bytes = std::stoull(it->second);
    }
    if (auto it = result.find("disk_cache_drop_pages"); it != result.end()) {
      disk_cache_drop_pages = !(it->second == "false" || it->second == "0");
    }
    if (auto it = result.find("disk_cache_fill_queue"); it != result.end()) {
      disk_cache_fill_queue = std::stoull(it->second);
    }
    if (auto it = result.find("disk_cache_mode"); it != result.end()) {
      disk_cache_mode = it->second;
    }
    if (auto it = result.find("disk_cache_entry_bytes"); it != result.end()) {
      disk_cache_entry_bytes = std::stoull(it->second);
    }
    if (auto it = result.find("disk_cache_admission"); it != result.end()) {
      disk_cache_admission = it->second;
    }
    if (auto it = result.find("disk_cache_admit_window"); it != result.end()) {
      disk_cache_admit_window = std::stoull(it->second);
    }
    if (disk_cache_mode != "file" && disk_cache_mode != "chunk") {
      throw std::runtime_error("disk_cache_mode must be \"file\" or \"chunk\", got \"" + disk_cache_mode + "\"");
    }
    if (disk_cache_admission != "always" && disk_cache_admission != "frequency") {
      throw std::runtime_error("disk_cache_admission must be \"always\" or \"frequency\", got \"" + disk_cache_admission + "\"");
    }
    if (disk_cache_entry_bytes < 4096 || (disk_cache_entry_bytes & (disk_cache_entry_bytes - 1)) != 0) {
      throw std::runtime_error("disk_cache_entry_bytes must be a power of two of at least 4096");
    }
    if (!disk_cache_dir.empty() && !sstable_backend_set) {
      throw std::runtime_error("disk_cache_dir requires sstable_backend: the tier fronts the object store that holds the SSTables");
    }
    if (!disk_cache_dir.empty() && disk_cache_bytes == 0) {
      throw std::runtime_error("disk_cache_dir requires disk_cache_bytes > 0");
    }

    auto tbt_it = result.find("trust_background_tail");
    if (tbt_it != result.end()) {
      trust_background_tail = (tbt_it->second == "true" || tbt_it->second == "1");
    }

    auto lin_it = result.find("linearizable_reads");
    if (lin_it != result.end()) {
      linearizable_reads = (lin_it->second == "true" || lin_it->second == "1");
    }
    if (linearizable_reads && !corfu_sync_writes) {
      // An acked put that still sits in the writer's buffer is invisible
      // to a fenced read, so the per-get guarantee would not hold.
      throw std::runtime_error(
          "config error: linearizable_reads requires corfu_sync_writes=true");
    }
    if (linearizable_reads && trust_background_tail) {
      throw std::runtime_error(
          "config error: linearizable_reads and trust_background_tail are "
          "mutually exclusive (one demands a fence per get, the other skips "
          "it)");
    }

    auto lt_it = result.find("log_trim_enabled");
    if (lt_it != result.end()) {
      log_trim_enabled = (lt_it->second == "true" || lt_it->second == "1");
    }
    if (auto it = result.find("log_trim_interval_ms"); it != result.end()) {
      log_trim_interval_ms = std::stoull(it->second);
    }
    if (auto it = result.find("log_trim_min_entries"); it != result.end()) {
      log_trim_min_entries = std::stoull(it->second);
    }
    if (auto it = result.find("log_trim_keep_checkpoints"); it != result.end()) {
      log_trim_keep_checkpoints = std::stoi(it->second);
    }
    if (auto it = result.find("checkpoint_dir"); it != result.end() && !it->second.empty()) {
      checkpoint_dir = it->second;
    }
    if (log_trim_enabled &&
        (backend_kind != BackendKind::kCorfu || !sstable_backend_set)) {
      throw std::runtime_error(
          "config error: log_trim_enabled needs backend=corfu and a separate "
          "sstable_backend (the checkpoint must not live in the log it trims)");
    }
    if (log_trim_keep_checkpoints < 2) log_trim_keep_checkpoints = 2;
  };
};
}  // namespace ozonedb
#endif  // METADATA_H