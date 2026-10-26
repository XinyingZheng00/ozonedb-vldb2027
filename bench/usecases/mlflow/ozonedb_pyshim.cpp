// C wrapper over ozonedb::DB, called from Python via ctypes
// (bench/ozonedb-paper-figures/figure18_mlflow/scripts/ozonedb_py.py), so no
// Python build dependency is needed. Unlike the JNI binding
// (ozonedb-jni-maven/native/src/main/cpp/jni_OzoneDBJNI.cpp), which keeps a
// thread_local DB*, each ozonedb_open() returns its own handle; the caller
// opens one per thread or process.
//
// Exposes only what the MLflow metric store needs: open/put/get/remove/close.

#include "db.h"

#include <cstring>

extern "C" {

// Returns an opaque DB* handle, or nullptr on failure.
void* ozonedb_open(const char* config_path) {
  ozonedb::DB* db = nullptr;
  ozonedb::Status status = ozonedb::DB::openDB(db, std::string(config_path));
  if (status != ozonedb::Status::kSuccess) {
    return nullptr;
  }
  return static_cast<void*>(db);
}

void ozonedb_close(void* handle) {
  if (handle == nullptr) return;
  ozonedb::DB* db = static_cast<ozonedb::DB*>(handle);
  ozonedb::DB::closeDB(db);
}

// Returns 0 (kSuccess), 1 (kFailure), 2 (kSealed), or 3 (kNotFound) --
// same ordering as ozonedb::Status.
int ozonedb_put(void* handle, const char* key, size_t key_len,
                 const char* value, size_t value_len) {
  ozonedb::DB* db = static_cast<ozonedb::DB*>(handle);
  ozonedb::Status status =
      db->put(std::string(key, key_len), std::string(value, value_len));
  return static_cast<int>(status);
}

int ozonedb_remove(void* handle, const char* key, size_t key_len) {
  ozonedb::DB* db = static_cast<ozonedb::DB*>(handle);
  ozonedb::Status status = db->remove(std::string(key, key_len));
  return static_cast<int>(status);
}

// On success (return 0), *out_value points to a newly heap-allocated
// buffer of length *out_len -- caller must free it with
// ozonedb_free_value(). *out_value is left untouched on failure.
int ozonedb_get(void* handle, const char* key, size_t key_len,
                 char** out_value, size_t* out_len) {
  ozonedb::DB* db = static_cast<ozonedb::DB*>(handle);
  std::string const* value = nullptr;
  ozonedb::Status status = db->get(std::string(key, key_len), value);
  if (status != ozonedb::Status::kSuccess) {
    return static_cast<int>(status);
  }
  char* buf = static_cast<char*>(malloc(value->size()));
  memcpy(buf, value->data(), value->size());
  *out_value = buf;
  *out_len = value->size();
  return 0;
}

void ozonedb_free_value(char* buf) {
  free(buf);
}

}  // extern "C"
