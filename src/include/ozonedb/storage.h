#ifndef STORAGE_H
#define STORAGE_H
#include "protobuf/record.pb.h"
#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <shared_mutex>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
namespace ozonedb {

enum class Status { kSuccess,
                    kFailure,
                    kSealed,
                    kNotFound };

// A shared-log backend signals a remote writer's APPEND or REMOVE of a
// file entry. The LogHandler uses it to keep its key index current.
enum class RemoteOp { kAppend,
                      kRemove };
// addr is the entry's position in the backend's global order (the Corfu
// global address). It ranks the records in the payload for LogKeyIndex.
// Backends with no global order pass -1.
using RemoteAppendListener =
    std::function<void(std::string const& file_name,
                       unsigned char const* data, size_t len, RemoteOp op,
                       long addr)>;

/**
 * @brief Base class of all storage backends. Base methods throw for
 * operations a backend does not override.
 */
class Storage {
 protected:
  std::string storage_path;

 public:
  Storage(std::string storage_path) {
    this->storage_path = storage_path;
  };
  virtual ~Storage() = default;

  /**
   * @brief Create a directory.
   *
   * @param name
   */
  virtual void createDirectory(std::string name) { throw std::runtime_error("createDirectory() is not implemented"); };

  /**
   * @brief Append bytes to a file.
   *
   * @return Status
   */
  virtual Status append(std::string const& fileName, unsigned char* const& data, int length) { throw std::runtime_error("append is not implemented"); };
  virtual Status appendNoFlush(std::string const& fileName, unsigned char* const& data, int length) { throw std::runtime_error("appendNoFlush is not implemented"); };
  virtual Status appendInBatch(std::string const& fileName, unsigned char* const& data, int length) { throw std::runtime_error("appendInBatch is not implemented"); };
  /**
   * @brief Flush a file.
   *
   * @param fileName
   * @return Status
   */
  virtual Status flush(std::string const& fileName) { throw std::runtime_error("flush is not implemented"); };

  /**
   * @brief Read all bytes of a file.
   *
   * @param data output buffer, owned by the caller
   * @param size output byte count
   * @return Status
   */
  virtual Status read(std::string const& fileName, unsigned char*& data, size_t& size) { throw std::runtime_error("read() is not implemented"); };

  /**
   * @brief Read length bytes from position a of a file.
   */
  virtual Status read(std::string const& fileName, unsigned char*& data, size_t a, size_t length) { throw std::runtime_error("read() is not implemented"); };

  /**
   * @brief Return the size of a file in bytes.
   */
  virtual size_t size(std::string fileName) { throw std::runtime_error("size() is not implemented"); };

  /**
   * @brief Make a file read-only. Idempotent.
   */
  virtual void seal(std::string fileName) { throw std::runtime_error("seal() is not implemented"); };

  /**
   * @brief Return true when the file is sealed.
   */
  virtual bool isSealed(std::string fileName) { throw std::runtime_error("isSealed() is not implemented"); };

  /**
   * @brief Delete a file.
   */
  virtual void remove(std::string fileName) { throw std::runtime_error("remove() is not implemented"); };

  /**
   * @brief Return true when the file exists.
   */
  virtual bool exist(std::string fileName) { throw std::runtime_error("exist() is not implemented"); };

  /**
   * @brief Register the callback for remote APPENDs and REMOVEs.
   * Default no-op. Only shared-log backends override it. Call once
   * before reads start.
   */
  virtual void setRemoteAppendListener(RemoteAppendListener /*listener*/) {}

  /**
   * @brief Global-order position of this thread's last successful append.
   *
   * Valid only directly after an append, appendInBatch or flush on the
   * same thread returned kSuccess. Returns -1 on backends with no global
   * order.
   */
  virtual long lastAppendAddressForThread() const { return -1; }

  /**
   * @brief Take a linearization fence for the calling thread.
   *
   * On a shared-log backend this samples the global tail once, waits for
   * the tailer to cover it, and records a thread-local fence token. Until
   * clearSync(), every read, size, exist and isSealed on this thread
   * reuses the token. Default no-op: direct backends (local FS, S3) are
   * always synced.
   *
   * Clear the token with SyncScope. A leaked token pins later operations
   * on this thread to a stale fence.
   */
  virtual void sync() {}
  virtual void clearSync() {}
  // True when the calling thread holds a live sync() token for this
  // storage. DB::get then reuses the caller's fence (DB::sync) and takes
  // none of its own.
  virtual bool hasSyncToken() const { return false; }

  // RAII for the sync() token: clears it on scope exit, also on early
  // return or exception.
  class SyncScope {
   public:
    explicit SyncScope(Storage& storage) : storage_(storage) { storage_.sync(); }
    ~SyncScope() { storage_.clearSync(); }
    SyncScope(SyncScope const&) = delete;
    SyncScope& operator=(SyncScope const&) = delete;

   private:
    Storage& storage_;
  };

#include <unistd.h>  // For fsync()
  int GetFileDescriptor(std::filebuf& filebuf) {
    class MyFileBuf : public std::filebuf {
     public:
      int handle() { return _M_file.fd(); }
    };

    return static_cast<MyFileBuf&>(filebuf).handle();
  }

 private:
  /**
   * @brief Move the stream to position.
   *
   * @param file
   * @param position
   */
  virtual void seek(std::ifstream& file, int position) { throw std::runtime_error("seek() is not implemented"); };
};

/**
 * @brief Local file system backend.
 */
class FileStorage : public Storage {
 public:
  explicit FileStorage(std::string path) : Storage(path) {
    // Create the directory if it does not exist.
    if (!std::filesystem::exists(path)) {
      if (!std::filesystem::create_directories(path)) {
        if (std::filesystem::exists(path)) {
          std::cerr << "Directory already created." << std::endl;
        } else {
          std::cerr << "Failed to create db directory." << std::endl;
        }
      }
    };
  };

  ~FileStorage() {
    for (auto& [key, value] : read_streams) {
      value->close();
      value.reset();
    }
    for (auto& [key, value] : write_streams) {
      value->close();
      value.reset();
    }
  };

  void createDirectory(std::string name);
  Status append(std::string const& fileName, unsigned char* const& data, int length);         // Append bytes to the file.
  Status appendNoFlush(std::string const& fileName, unsigned char* const& data, int length);  // Append without a flush. Only the SSTable writer uses it.
  Status appendInBatch(std::string const& fileName, unsigned char* const& data, int length) {
    return append(fileName, data, length);
  };
  Status flush(std::string const& fileName);                                                // Flush the file.
  Status read(std::string const& fileName, unsigned char*& data, size_t& size);             // Read all bytes of the file.
  Status read(std::string const& fileName, unsigned char*& data, size_t a, size_t length);  // Read length bytes from position a.
  size_t size(std::string fileName);                                                        // Size of the file in bytes.
  void seal(std::string fileName);                                                          // Seal the file.
  bool isSealed(std::string fileName);                                                      // True when the file is sealed.
  void remove(std::string fileName);                                                        // Delete the file.
  bool exist(std::string fileName);                                                         // True when the file exists.
 private:
  void seek(std::ifstream& file, int position);  // Move the stream to position.
  std::unordered_map<std::string, std::unique_ptr<std::ifstream>> read_streams;
  std::unordered_map<std::string, std::unique_ptr<std::ofstream>> write_streams;
  std::shared_mutex read_mtx;
  std::shared_mutex write_mtx;

  // One mutex per file serializes appends. getWriteStream hands the same
  // ofstream to every caller, and an append is write + flush + fsync, so
  // concurrent appends would interleave bytes. The storage layer owns
  // its own concurrency.
  std::mutex append_map_mtx_;
  std::unordered_map<std::string, std::unique_ptr<std::mutex>> append_mtx_;
  std::mutex& appendMutexFor(std::string const& name);
  // One mutex per file serializes reads: getReadStream hands the same
  // ifstream to every caller, and a read is seek + read. read_mtx above
  // guards only the map.
  std::mutex read_map_mtx_;
  std::unordered_map<std::string, std::unique_ptr<std::mutex>> read_file_mtx_;
  std::mutex& readMutexFor(std::string const& name);

 public:
  std::ifstream* getReadStream(std::string const& name);
  std::ofstream* getWriteStream(std::string const& name);
};
}  // namespace ozonedb
#endif  // STORAGE_H
