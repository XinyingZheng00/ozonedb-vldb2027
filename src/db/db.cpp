#include "db.h"
#include "read_profile.h"
#include "disk_cache_storage.h"
#include "helper.h"
#include "thread_pool.h"
#ifdef OZONEDB_ENABLE_CORFU
#include "corfu_storage.h"
#include "log_trimmer.h"
#endif
#ifdef OZONEDB_ENABLE_S3
#include "s3_storage.h"
#endif
#include <algorithm>
#include <optional>
namespace ozonedb {

namespace {
// Build a Storage for one backend kind. DB::DB calls it for the log
// storage and, with sstable_backend set, again for the SSTable storage
// (paper §3.5). Only the Corfu backend reads checkpoint_store: it loads the
// newest checkpoint from it at open. Null means replay from address 0.
Storage* makeStorage(BackendKind kind, Metadata const& md, bool for_sstables,
                     Storage* checkpoint_store) {
  switch (kind) {
    case BackendKind::kLocal:
      return new FileStorage(for_sstables && !md.sstable_dir.empty()
                                 ? md.sstable_dir
                                 : md.DBpath);
    case BackendKind::kCorfu:
#ifdef OZONEDB_ENABLE_CORFU
    {
      CorfuClientOptions co;
      co.endpoint = md.corfu_endpoint;
      co.stream_name = md.corfu_stream_name;
      co.read_batch = md.corfu_read_batch;
      co.idle_poll_ms = md.corfu_idle_poll_ms;
      co.hole_fill_timeout_ms = md.corfu_hole_fill_timeout_ms;
      co.request_timeout_ms = md.corfu_request_timeout_ms;
      auto* corfu = new CorfuDBStorage(co, md.DBpath, checkpoint_store, md.checkpoint_dir,
                                       md.corfu_fast_ack);
      // Write mode: sync (default) acks a put once it is sequenced; async
      // acks from the local buffer and submits every commit interval.
      corfu->setSyncMode(md.corfu_sync_writes);
      corfu->setCommitInterval(md.corfu_commit_interval_ms);
      return corfu;
    }
#else
      throw std::runtime_error(
          "OzoneDB was built without CorfuDB support (rebuild with "
          "-DOZONEDB_ENABLE_CORFU=ON)");
#endif
    case BackendKind::kS3:
#ifdef OZONEDB_ENABLE_S3
      return new S3Storage(md.s3_endpoint, md.s3_region, md.s3_bucket,
                           md.s3_access_key, md.s3_secret_key,
                           md.s3_use_path_style,
                           for_sstables ? md.sstable_dir : md.DBpath);
#else
      throw std::runtime_error(
          "OzoneDB was built without S3 support (rebuild with "
          "-DOZONEDB_ENABLE_S3=ON)");
#endif
  }
  throw std::runtime_error("makeStorage: unhandled BackendKind");
}
}  // namespace

DB::DB(std::string const& shared_config_path) {
  this->metadata = new Metadata(shared_config_path);
  if (this->metadata->mode == 0) {
    this->mode = Mode::Singleton;
  } else {
    this->mode = Mode::MultipleProcesses;
  }

  // Build sstable_storage first: CorfuDBStorage::bootstrap loads the
  // newest checkpoint from it.
  if (this->metadata->sstable_backend_set) {
    this->sstable_storage = makeStorage(this->metadata->sstable_backend_kind,
                                        *this->metadata, /*for_sstables=*/true,
                                        /*checkpoint_store=*/nullptr);
  }
  // Disk tier in front of the SSTable store. Metadata rejects disk_cache_dir
  // without sstable_backend, so sstable_storage is non-null here.
  if (!this->metadata->disk_cache_dir.empty()) {
    DiskCacheStorage::Options o;
    o.dir = this->metadata->disk_cache_dir;
    o.capacity_bytes = this->metadata->disk_cache_bytes;
    o.prefix = this->metadata->sstable_level_prefix;
    o.chunk_bytes = static_cast<size_t>(this->metadata->disk_cache_chunk_bytes);
    o.drop_pages = this->metadata->disk_cache_drop_pages;
    o.max_queue = static_cast<size_t>(this->metadata->disk_cache_fill_queue);
    o.mode = this->metadata->disk_cache_mode == "chunk" ? DiskCacheStorage::Mode::kChunk : DiskCacheStorage::Mode::kFile;
    o.entry_bytes = static_cast<size_t>(this->metadata->disk_cache_entry_bytes);
    o.admission = this->metadata->disk_cache_admission == "frequency" ? DiskCacheStorage::Admission::kFrequency : DiskCacheStorage::Admission::kAlways;
    o.admit_window = this->metadata->disk_cache_admit_window;
    auto* tier = new DiskCacheStorage(std::unique_ptr<Storage>(this->sstable_storage), o);
    this->sstable_storage = tier;
    this->disk_cache = tier;
  }
  this->log_storage = makeStorage(this->metadata->backend_kind,
                                  *this->metadata, /*for_sstables=*/false,
                                  /*checkpoint_store=*/this->sstable_storage);
  if (!this->metadata->sstable_backend_set) {
    // No sstable_backend: the two pointers alias.
    this->sstable_storage = this->log_storage;
  }

  this->lru_cache = new LRUCache(this->metadata->lru_cache_bytes,
                                 this->log_storage, this->sstable_storage);
  this->metadata_log = new MetadataLogHandler(this->metadata->metadata_log, this->log_storage);
  this->metadata_log->setLRUCache(this->lru_cache);
  if (this->disk_cache != nullptr) {
    DiskCacheStorage* tier = this->disk_cache;
    this->metadata_log->setSSTableRemovedListener([tier](std::string const& file_name) { tier->invalidate(file_name); });
  }
  this->metadata_log->setMetadata(this->metadata);
  {
    // Cache warm policy and liveness check, set before any thread runs.
    // openDB starts the warm worker. closeDB stops it before the counters print.
    LRUCache::WarmPolicy policy;
    policy.enabled = this->metadata->cache_warm_enabled;
    policy.max_level = this->metadata->cache_warm_max_level;
    policy.max_fraction = this->metadata->cache_warm_max_fraction;
    policy.min_input_blocks = static_cast<size_t>(this->metadata->cache_warm_min_input_blocks);
    policy.read_bytes = static_cast<size_t>(this->metadata->compaction_read_bytes);
    this->lru_cache->setWarmPolicy(policy);
    MetadataLogHandler* mlh = this->metadata_log;
    this->lru_cache->setLiveFileCheck([mlh](std::string const& file_name) {
      // The shared_ptr snapshot keeps the View alive for the check.
      auto view = mlh->latestViewSnapshot();
      return view != nullptr && view->key_range.find(file_name) != view->key_range.end();
    });
  }
  // The key index is on for all backends. On Corfu the remote-append
  // listener (LogHandler::onRemoteAppend) adds peer writes. On other
  // backends only the writer path updates it.
  this->log_handler = new LogHandler(
      this->metadata->log_file_size_limit, this->metadata->log_prefix,
      this->log_storage, lru_cache, metadata_log, /*enable_key_index=*/true,
      /*key_index_capacity=*/1000000,
      /*trust_background_tail=*/this->metadata->trust_background_tail,
      /*linearizable_reads=*/this->metadata->linearizable_reads);
  this->sstable_handler = new SSTableHandler(this->sstable_storage, metadata_log, this->metadata->sstable_level_prefix, lru_cache);
  this->sstable_handler->setMaxLevel(this->metadata->max_level);
  this->thread_pool = new ThreadPool(std::thread::hardware_concurrency());
  this->log_handler->setThreadPool(this->thread_pool);
  this->sstable_handler->setThreadPool(this->thread_pool);
  this->fingerprint = generateFingerprint();
  this->watcher = new CompactionWatcher(this->metadata, this->log_storage, this->log_handler, metadata_log, this->sstable_handler, fingerprint);
  this->watcher->setSSTableStorage(this->sstable_storage);
  this->watcher->setLRUCache(this->lru_cache);
  auto* task_log_handler = new TaskLogHandler(this->metadata->task_log, this->log_storage);
  task_log_handler->setHeartbeatThreshold(this->metadata->task_heartbeat_threshold_ticks);
  this->watcher->setTaskLogHandler(task_log_handler);
  this->watcher->setMode(this->mode);
  this->file_mutex_manager = new FileMutexManager();
  this->lru_cache->setFileMutexManager(this->file_mutex_manager);
  this->watcher->setFileMutexManager(this->file_mutex_manager);
  this->watcher->setThreadPool(this->thread_pool);
};

DB::~DB() {
#ifdef OZONEDB_ENABLE_CORFU
  delete this->trimmer;  // Uses both stores, so it goes first.
#endif
  delete this->watcher;
  delete this->sstable_handler;
  delete this->log_handler;
  delete this->lru_cache;
  delete this->metadata_log;
  // The two pointers alias without sstable_backend: guard the double-free.
  // When split, delete sstable_storage first so pending writes flush.
  if (this->sstable_storage != this->log_storage) {
    delete this->sstable_storage;
  }
  delete this->log_storage;
  delete this->metadata;
  delete this->thread_pool;
  delete this->file_mutex_manager;
}

Status DB::openDB(DB*& db, std::string const& shared_config_path) {
  db = new DB(shared_config_path);
  db->active = true;
  db->metadata_log->rollForwardMetadataLog();
  // Reconcile the disk tier and start its fill worker before
  // initSSTMetadata, which opens every live SSTable. reconcile() needs only
  // the rolled-forward View.
  if (db->disk_cache != nullptr) {
    std::shared_ptr<View const> view = db->metadata_log->latestViewSnapshot();
    size_t const removed = db->disk_cache->reconcile([view](std::string const& name, size_t bytes) {
      if (!view || view->key_range.find(name) == view->key_range.end()) return false;
      auto sz = view->file_size.find(name);
      return sz == view->file_size.end() || sz->second == bytes;
    });
    std::cerr << "[disk_cache] reconciled " << db->disk_cache->stats().files << " files, removed " << removed << "\n";
    db->disk_cache->startFillWorker();
  }
  db->metadata_log->initSSTMetadata();
  // Seed the key index from log files the rollforward cached. Set
  // latest_view on log_handler and lru_cache first: warmKeyIndex calls
  // cache->checkReadMoreLog, which reads the cache's latest_view. The DB
  // pins the snapshot so the raw pointers stay valid after openDB.
  db->latest_view_snapshot = db->metadata_log->latestViewSnapshot();
  View const* view_ptr = db->latest_view_snapshot.get();
  db->log_handler->setLatestView(view_ptr);
  db->lru_cache->setLatestView(view_ptr);
  db->lru_cache->startWarmWorker();
  db->log_handler->warmKeyIndex();
  if (db->metadata->compaction_enabled) {
    db->watcher->startCompactionWatcher(&(db->active));
  }
  db->metadata_log->startViewUpdate(&(db->active));
#ifdef OZONEDB_ENABLE_CORFU
  if (db->metadata->log_trim_enabled) {
    auto* corfu = dynamic_cast<CorfuDBStorage*>(db->log_storage);
    if (corfu != nullptr && db->sstable_storage != db->log_storage) {
      LogTrimmer::Options options;
      options.dir = db->metadata->checkpoint_dir;
      options.interval_ms = db->metadata->log_trim_interval_ms;
      options.min_entries = db->metadata->log_trim_min_entries;
      options.keep = db->metadata->log_trim_keep_checkpoints;
      options.creator = db->fingerprint;
      db->trimmer = new LogTrimmer(corfu, db->sstable_storage, options);
      db->trimmer->start();
    }
  }
#endif
  return Status::kSuccess;
}

Status DB::closeDB(DB*& db) {
  db->active = false;
  db->watcher->stopCompactionWatcher();
  db->metadata_log->stopViewUpdate();
#ifdef OZONEDB_ENABLE_CORFU
  // Final checkpoint and trim before the stores go away.
  if (db->trimmer != nullptr) db->trimmer->stop();
#endif
  // Join the workers before the counters print. No thread touches the
  // stores after this point.
  db->lru_cache->stopWarmWorker();
  if (db->disk_cache != nullptr) db->disk_cache->stopFillWorker();
  db->lru_cache->printCacheStats();
  if (db->disk_cache != nullptr) db->disk_cache->printStats();
  OZONEDB_PROFILE_DUMP();
  delete db;
  return Status::kSuccess;
}

Status DB::put(std::string const& key, std::string const& value) {
  Record record;
  record.set_key(key);
  record.set_value(value);
  record.set_type(kTypeValue);
  // Return the append status. A put the log did not take is not a success.
  return log_handler->addRecord(record);
}

Status DB::sync() {
  log_storage->sync();
  return Status::kSuccess;
}

Status DB::clearSync() {
  log_storage->clearSync();
  return Status::kSuccess;
}

Status DB::remove(std::string const& key) {
  Record record;
  record.set_key(key);
  record.set_type(kTypeDeletion);
  // A tombstone the log did not take is a delete that never happened.
  return this->log_handler->addRecord(record);
}

Status DB::get(std::string const& key, std::string const*& value,
               std::shared_ptr<Record>& guard) {
  // Fast path. With trust_background_tail the key index is authoritative
  // for log reads: addRecord and, on Corfu, onRemoteAppend keep it current.
  // A hit skips the View snapshot and the fence. A tombstone ends the read.
  if (this->metadata->trust_background_tail) {
    std::shared_ptr<Record> fast_hit;
    if (this->log_handler->tryIndexLookup(key, fast_hit)) {
      OZONEDB_PROFILE_COUNT(read_profile::index_hits);
      if (fast_hit->type() == kTypeDeletion) return Status::kFailure;
      value = &(fast_hit->value());
      guard = std::move(fast_hit);
      return Status::kSuccess;
    }
  }

  // Strict mode (linearizable_reads): one fence per get. SyncScope waits
  // for the tailer once, the linearization point, and records a
  // thread-local fence token. Every storage call in this get reuses the
  // token: syncView, checkReadMoreLog, the inline data-log scan and the
  // post-scan size check.
  //
  // The post-scan check guards against a peer compaction that removes a
  // log file under the scan. The tailer applies entries in address order,
  // so the COMPACT before that REMOVE has grown the local metadata log past
  // view_offset. The size check sees it and the get retries. A retry does
  // not re-fence: the original fence stays the linearization point. The
  // attempt cap serves the last result instead of spinning.
  //
  // A token from DB::sync takes precedence: the get then linearizes at the
  // caller's fence and takes none of its own.
  OZONEDB_PROFILE_COUNT(read_profile::reads);
  OZONEDB_PROFILE_SCOPE(profile_total, read_profile::total_ns);
  bool const strict = this->metadata->linearizable_reads;
  std::optional<Storage::SyncScope> fence;
  if (strict && !log_storage->hasSyncToken()) {
    fence.emplace(*log_storage);
  }
  // Default mode uses the periodic View snapshot. If a peer compaction
  // removes a listed file after the snapshot, the read of that file fails
  // and the output file is not in the snapshot. The cache counts failed
  // file reads. When the scan found nothing and that counter moved, the
  // get rolls the View forward (one fence) and scans again. The cap bounds
  // a broken store.
  constexpr int kStrictMaxAttempts = 5;
  constexpr int kDefaultMaxAttempts = 5;
  for (int attempt = 0;; ++attempt) {
    size_t view_offset = 0;
    View const* view_ptr = nullptr;
    uint64_t read_failures_before = 0;
    {
      OZONEDB_PROFILE_SCOPE(profile_view, read_profile::view_ns);
      if (strict) {
        view_offset = metadata_log->syncView();
      }
      read_failures_before = lru_cache->readFailures();

      // Atomic View snapshot: no deep copy, no lock. The DB holds it so
      // the children's raw latest_view pointers stay valid during the get.
      this->latest_view_snapshot = metadata_log->latestViewSnapshot();
      view_ptr = this->latest_view_snapshot.get();
      if (view_ptr == nullptr) {
        return Status::kFailure;
      }
      this->log_handler->setLatestView(view_ptr);
      this->sstable_handler->setLatestView(view_ptr);
      this->lru_cache->setLatestView(view_ptr);
    }

    // The offset arguments are unused and always empty.
    std::shared_ptr<Record> log_record;
    std::string const empty_offset;
    std::string latest_offset;
    {
      OZONEDB_PROFILE_SCOPE(profile_log, read_profile::log_ns);
      log_handler->readRecord(key, log_record, empty_offset, latest_offset);
    }

    std::shared_ptr<Record> latest_record;
    if (log_record) {
      latest_record = std::move(log_record);
    } else {
      std::shared_ptr<Record> sstable_record;
      {
        OZONEDB_PROFILE_COUNT(read_profile::sst_calls);
        OZONEDB_PROFILE_SCOPE(profile_sst, read_profile::sst_ns);
        sstable_handler->readRecordFromAllLevel(key, sstable_record, empty_offset);
      }
      if (sstable_record) {
        latest_record = std::move(sstable_record);
      }
    }

    if (strict && attempt + 1 < kStrictMaxAttempts &&
        log_storage->size(this->metadata->metadata_log) != view_offset) {
      continue;
    }
    if (!strict && !latest_record && attempt + 1 < kDefaultMaxAttempts &&
        lru_cache->readFailures() != read_failures_before) {
      metadata_log->syncView();
      continue;
    }

    if (latest_record) {
      if (latest_record->type() == kTypeDeletion) {
        return Status::kFailure;
      }
      value = &(latest_record->value());
      guard = std::move(latest_record);
      return Status::kSuccess;
    }
    return Status::kFailure;
  }
}
}  // namespace ozonedb
