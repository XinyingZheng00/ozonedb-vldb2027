#ifdef OZONEDB_ENABLE_S3
#include "s3_storage.h"

#include <aws/core/auth/AWSCredentialsProvider.h>
#include <aws/core/client/ClientConfiguration.h>
#include <aws/core/utils/memory/stl/AWSString.h>
#include <aws/core/utils/stream/PreallocatedStreamBuf.h>
#include <aws/s3/model/DeleteObjectRequest.h>
#include <aws/s3/model/GetObjectRequest.h>
#include <aws/s3/model/HeadObjectRequest.h>
#include <aws/s3/model/PutObjectRequest.h>
#include <cstring>
#include <iostream>
#include <sstream>

namespace ozonedb {

namespace {

std::atomic<int> g_aws_init_refcount{0};
std::mutex g_aws_init_mtx;
Aws::SDKOptions g_aws_options;

void ensureAwsInit() {
  std::lock_guard<std::mutex> lock(g_aws_init_mtx);
  if (g_aws_init_refcount.fetch_add(1) == 0) {
    Aws::InitAPI(g_aws_options);
  }
}

void maybeAwsShutdown() {
  std::lock_guard<std::mutex> lock(g_aws_init_mtx);
  if (g_aws_init_refcount.fetch_sub(1) == 1) {
    Aws::ShutdownAPI(g_aws_options);
  }
}

// Wrap bytes as the std::shared_ptr<Aws::IOStream> a PutObject body takes.
std::shared_ptr<Aws::IOStream> makeBodyStream(unsigned char const* data, size_t size) {
  auto stream = Aws::MakeShared<Aws::StringStream>("S3Body");
  stream->write(reinterpret_cast<char const*>(data), static_cast<std::streamsize>(size));
  return stream;
}

}  // namespace

S3Storage::S3Storage(std::string const& endpoint,
                     std::string const& region,
                     std::string const& bucket,
                     std::string const& access_key,
                     std::string const& secret_key,
                     bool use_path_style,
                     std::string const& db_path)
    : Storage(db_path), bucket_(bucket) {
  ensureAwsInit();
  owns_aws_sdk_ = true;

  Aws::Client::ClientConfiguration cfg;
  cfg.region = region.empty() ? Aws::String("us-east-1") : Aws::String(region.c_str());
  if (!endpoint.empty()) {
    cfg.endpointOverride = Aws::String(endpoint.c_str());
    // A custom endpoint (MinIO, R2, Wasabi, SeaweedFS) is a plain HTTP
    // host, not an AWS service DNS name. With use_path_style the client
    // sends `http://host:port/bucket/key`. For AWS proper, leave endpoint
    // empty and set use_path_style=false.
    if (endpoint.rfind("http://", 0) == 0) {
      cfg.scheme = Aws::Http::Scheme::HTTP;
      cfg.verifySSL = false;
    }
  }

  if (!access_key.empty()) {
    Aws::Auth::AWSCredentials creds(Aws::String(access_key.c_str()),
                                    Aws::String(secret_key.c_str()));
    client_ = std::make_unique<Aws::S3::S3Client>(
        creds, cfg,
        Aws::Client::AWSAuthV4Signer::PayloadSigningPolicy::Never,
        /*useVirtualAddressing=*/!use_path_style);
  } else {
    // No keys: use the default credential provider chain (env vars,
    // instance profile).
    client_ = std::make_unique<Aws::S3::S3Client>(
        cfg,
        Aws::Client::AWSAuthV4Signer::PayloadSigningPolicy::Never,
        /*useVirtualAddressing=*/!use_path_style);
  }
}

S3Storage::~S3Storage() {
  // Flush pending batched writes.
  std::unique_lock<std::mutex> lock(mtx_);
  if (!cached_file_.empty()) {
    std::vector<std::string> keys;
    keys.reserve(cached_file_.size());
    for (auto const& kv : cached_file_) keys.push_back(kv.first);
    lock.unlock();
    for (auto const& k : keys) {
      flushLocked(k);
    }
  }
  client_.reset();
  if (owns_aws_sdk_) maybeAwsShutdown();
}

std::string S3Storage::keyFor(std::string const& fileName) const {
  if (storage_path.empty()) return fileName;
  if (storage_path.back() == '/' || fileName.front() == '/') {
    return storage_path + fileName;
  }
  return storage_path + "/" + fileName;
}

void S3Storage::createDirectory(std::string /*name*/) {
  // S3 has no directory concept; no-op.
}

Status S3Storage::flushLocked(std::string const& fileName) {
  std::unique_lock<std::mutex> lock(mtx_);
  auto it = cached_file_.find(fileName);
  if (it == cached_file_.end()) return Status::kNotFound;
  std::vector<unsigned char> payload = std::move(it->second);
  cached_file_.erase(it);
  lock.unlock();

  Aws::S3::Model::PutObjectRequest req;
  req.SetBucket(Aws::String(bucket_.c_str()));
  req.SetKey(Aws::String(keyFor(fileName).c_str()));
  req.SetBody(makeBodyStream(payload.data(), payload.size()));

  auto outcome = client_->PutObject(req);
  if (!outcome.IsSuccess()) {
    std::cerr << "[s3] PutObject failed for " << keyFor(fileName) << ": "
              << outcome.GetError().GetMessage() << "\n";
    return Status::kFailure;
  }
  return Status::kSuccess;
}

Status S3Storage::append(std::string const& fileName, unsigned char* const& data, int length) {
  // S3 has no append. The three logs must use another backend; this
  // tier is for SSTables (paper §3.5). append is buffer + immediate flush.
  //
  // flushLocked moves the buffer out and erases the map entry, so a
  // second append would put only the new bytes. Keep the accumulated
  // bytes and re-put the whole object on every call. That makes append
  // O(object size) per call: correct, not fast.
  std::vector<unsigned char> snapshot;
  {
    std::lock_guard<std::mutex> lk(mtx_);
    if (sealed_files_.count(fileName)) return Status::kSealed;
    auto& buf = cached_file_[fileName];
    buf.insert(buf.end(), data, data + length);
    snapshot = buf;  // flushLocked moves the buffer out
  }
  Status s = flushLocked(fileName);
  if (s != Status::kSuccess) return s;
  // flushLocked erased the entry; restore it so the next append re-puts
  // the whole object.
  {
    std::lock_guard<std::mutex> lk(mtx_);
    cached_file_[fileName] = std::move(snapshot);
  }
  return Status::kSuccess;
}

Status S3Storage::appendNoFlush(std::string const& fileName, unsigned char* const& data, int length) {
  std::lock_guard<std::mutex> lk(mtx_);
  if (sealed_files_.count(fileName)) return Status::kSealed;
  auto& buf = cached_file_[fileName];
  buf.insert(buf.end(), data, data + length);
  return Status::kSuccess;
}

Status S3Storage::appendInBatch(std::string const& fileName, unsigned char* const& data, int length) {
  // Accumulate only; flush() sends the whole object in one PutObject.
  // One SSTable is one object.
  return appendNoFlush(fileName, data, length);
}

Status S3Storage::flush(std::string const& fileName) {
  return flushLocked(fileName);
}

Status S3Storage::read(std::string const& fileName, unsigned char*& data, size_t& size) {
  Aws::S3::Model::GetObjectRequest req;
  req.SetBucket(Aws::String(bucket_.c_str()));
  req.SetKey(Aws::String(keyFor(fileName).c_str()));
  auto outcome = client_->GetObject(req);
  if (!outcome.IsSuccess()) return Status::kFailure;

  auto& body = outcome.GetResult().GetBody();
  body.seekg(0, std::ios::end);
  std::streampos total = body.tellg();
  body.seekg(0, std::ios::beg);
  size = static_cast<size_t>(total);
  data = new unsigned char[size];
  body.read(reinterpret_cast<char*>(data), static_cast<std::streamsize>(size));
  return Status::kSuccess;
}

Status S3Storage::read(std::string const& fileName, unsigned char*& data, size_t a, size_t length) {
  Aws::S3::Model::GetObjectRequest req;
  req.SetBucket(Aws::String(bucket_.c_str()));
  req.SetKey(Aws::String(keyFor(fileName).c_str()));
  std::ostringstream range;
  range << "bytes=" << a << "-" << (a + length - 1);
  req.SetRange(Aws::String(range.str().c_str()));
  auto outcome = client_->GetObject(req);
  if (!outcome.IsSuccess()) return Status::kFailure;

  auto& body = outcome.GetResult().GetBody();
  data = new unsigned char[length];
  body.read(reinterpret_cast<char*>(data), static_cast<std::streamsize>(length));
  return Status::kSuccess;
}

size_t S3Storage::size(std::string fileName) {
  {
    // Bytes in the local buffer are not PUT yet; a reader of a
    // just-written file must still see the full size.
    std::lock_guard<std::mutex> lk(mtx_);
    auto it = cached_file_.find(fileName);
    if (it != cached_file_.end() && !it->second.empty()) {
      // The remote object may not exist yet. Return the buffer size; a
      // caller about to read flushes first.
      return it->second.size();
    }
  }
  Aws::S3::Model::HeadObjectRequest req;
  req.SetBucket(Aws::String(bucket_.c_str()));
  req.SetKey(Aws::String(keyFor(fileName).c_str()));
  auto outcome = client_->HeadObject(req);
  if (!outcome.IsSuccess()) return 0;
  return static_cast<size_t>(outcome.GetResult().GetContentLength());
}

void S3Storage::seal(std::string fileName) {
  // Flush pending data first, so the next GetObject sees the final
  // object. S3 objects are immutable once PUT; the seal is local and
  // only stops further appends.
  flushLocked(fileName);
  std::lock_guard<std::mutex> lk(mtx_);
  sealed_files_.insert(fileName);
}

bool S3Storage::isSealed(std::string fileName) {
  {
    std::lock_guard<std::mutex> lk(mtx_);
    if (sealed_files_.count(fileName)) return true;
  }
  // A file written by another process: an object on S3 with no local
  // buffer counts as sealed, because SSTables are write-once.
  return exist(fileName);
}

void S3Storage::remove(std::string fileName) {
  {
    std::lock_guard<std::mutex> lk(mtx_);
    cached_file_.erase(fileName);
    sealed_files_.erase(fileName);
  }
  Aws::S3::Model::DeleteObjectRequest req;
  req.SetBucket(Aws::String(bucket_.c_str()));
  req.SetKey(Aws::String(keyFor(fileName).c_str()));
  client_->DeleteObject(req);
}

bool S3Storage::exist(std::string fileName) {
  {
    std::lock_guard<std::mutex> lk(mtx_);
    if (cached_file_.count(fileName)) return true;
  }
  Aws::S3::Model::HeadObjectRequest req;
  req.SetBucket(Aws::String(bucket_.c_str()));
  req.SetKey(Aws::String(keyFor(fileName).c_str()));
  auto outcome = client_->HeadObject(req);
  return outcome.IsSuccess();
}

}  // namespace ozonedb
#endif  // OZONEDB_ENABLE_S3
