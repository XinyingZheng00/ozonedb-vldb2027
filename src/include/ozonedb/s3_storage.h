#ifndef S3_STORAGE_H
#define S3_STORAGE_H
#ifdef OZONEDB_ENABLE_S3
#include "storage.h"
#include <aws/core/Aws.h>
#include <aws/s3/S3Client.h>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace ozonedb {

/**
 * @brief Storage backend on an S3-compatible object store.
 *
 * Works with any service that speaks the S3 API (AWS S3, MinIO, R2,
 * Wasabi, B2, SeaweedFS) through the endpoint override and path-style
 * addressing. It is the regular-storage tier for SSTables (paper §3.5):
 * SSTables are immutable, read by byte range, and never appended to.
 *
 * Write path: `appendInBatch` / `appendNoFlush` buffer bytes per file
 * name in memory. `flush` does one `PutObject` of the whole buffer.
 * `append` buffers and flushes at once. S3 has no append primitive, so
 * files that need atomic append (the three logs) must use another backend.
 *
 * Read path: `read(name, data, size)` is a full `GetObject`. The ranged
 * variant uses a `Range` header. `size(name)` is `HeadObject` Content-Length.
 *
 * `seal` / `isSealed` are tracked locally. S3 objects are immutable once
 * PUT, so there is no server-side seal marker.
 */
class S3Storage : public Storage {
 public:
  S3Storage(std::string const& endpoint,
            std::string const& region,
            std::string const& bucket,
            std::string const& access_key,
            std::string const& secret_key,
            bool use_path_style,
            std::string const& db_path);
  ~S3Storage() override;

  void createDirectory(std::string name) override;
  Status append(std::string const& fileName, unsigned char* const& data, int length) override;
  Status appendNoFlush(std::string const& fileName, unsigned char* const& data, int length) override;
  Status appendInBatch(std::string const& fileName, unsigned char* const& data, int length) override;
  Status flush(std::string const& fileName) override;
  Status read(std::string const& fileName, unsigned char*& data, size_t& size) override;
  Status read(std::string const& fileName, unsigned char*& data, size_t a, size_t length) override;
  size_t size(std::string fileName) override;
  void seal(std::string fileName) override;
  bool isSealed(std::string fileName) override;
  void remove(std::string fileName) override;
  bool exist(std::string fileName) override;

 private:
  // Form the full S3 key (storage_path is the db-path prefix inside the bucket).
  std::string keyFor(std::string const& fileName) const;

  // Flush the buffered bytes for this file as one PutObject. Takes mtx_;
  // the caller must not hold it.
  Status flushLocked(std::string const& fileName);

  std::unique_ptr<Aws::S3::S3Client> client_;
  std::string bucket_;
  Aws::SDKOptions aws_options_;
  bool owns_aws_sdk_ = false;

  std::mutex mtx_;
  // Per-file write buffer for appendInBatch.
  std::unordered_map<std::string, std::vector<unsigned char>> cached_file_;
  // Names seal() was called on locally. Later appends to them return
  // kSealed, per the Storage contract.
  std::unordered_set<std::string> sealed_files_;
};

}  // namespace ozonedb
#endif  // OZONEDB_ENABLE_S3
#endif  // S3_STORAGE_H
