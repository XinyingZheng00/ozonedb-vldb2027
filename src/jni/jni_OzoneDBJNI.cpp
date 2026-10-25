#include "jni_OzoneDBJNI.h"
#include "db.h"
#include <chrono>
#include <ctime>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>

// One DB per thread: YCSB -threads N opens N databases in one JVM.
thread_local ozonedb::DB* db_instance = nullptr;
class EventListenerOzonedb : public ozonedb::EventListener {
  private:
  std::string getCurrentTimestamp() {
    using namespace std::chrono;
    auto now = system_clock::now();
    auto now_time_t = system_clock::to_time_t(now);
    auto ms = duration_cast<milliseconds>(now.time_since_epoch()) % 1000;

    std::ostringstream oss;
    oss << std::put_time(std::gmtime(&now_time_t), "%Y-%m-%d %H:%M:%S")
        << ':' << std::setw(3) << std::setfill('0') << ms.count();
    return oss.str();
  }

 public:
// Log and SSTable compaction events are printed the same way.
  void onLogCompactionStart() {
    std::cout << getCurrentTimestamp() << " - Compaction started: " << std::endl;
  }

  void onLogCompactionCompletion(int time_ms) {
    std::cout << getCurrentTimestamp() << " - Compaction completed: " << std::endl;
  }

  void onSSTableCompactionStart() {
    std::cout << getCurrentTimestamp() << " - Compaction started: " << std::endl;
  }

  void onSSTableCompactionCompletion(int time_ms, int source_level) {
    std::cout << getCurrentTimestamp() << " - Compaction completed: " << std::endl;
  }

  void onNewTail() {
    std::cout << getCurrentTimestamp() << " - NewTail completed: " << std::endl;
  };

};


namespace {
// Turn a C++ exception into a Java RuntimeException. An exception that
// escapes a JNI entry point terminates the JVM, so every entry point converts.
void throwJavaRuntime(JNIEnv* env, char const* what) {
  if (env->ExceptionCheck()) return;  // a pending Java exception wins
  jclass cls = env->FindClass("java/lang/RuntimeException");
  if (cls != nullptr) env->ThrowNew(cls, what);
}
}  // namespace

// Wrap an entry point body. ret is the value to return after throwing;
// empty for void entry points.
#define OZONEDB_JNI_TRY try {
#define OZONEDB_JNI_CATCH(env, ret)                                  \
  }                                                                  \
  catch (std::exception const & e) {                                 \
    throwJavaRuntime(env, e.what());                                 \
    return ret;                                                      \
  }                                                                  \
  catch (...) {                                                      \
    throwJavaRuntime(env, "unknown native exception in OzoneDB JNI"); \
    return ret;                                                      \
  }

JNIEXPORT void JNICALL Java_jni_OzoneDBJNI_openDB(JNIEnv* env, jobject obj, jstring configPath) {
  OZONEDB_JNI_TRY
  char const* nativeConfigPath = env->GetStringUTFChars(configPath, 0);
  ozonedb::Status status = ozonedb::DB::openDB(db_instance, std::string(nativeConfigPath));
  env->ReleaseStringUTFChars(configPath, nativeConfigPath);

  // openDB leaves db_instance null on failure.
  if (status != ozonedb::Status::kSuccess || db_instance == nullptr) {
    throwJavaRuntime(env, "OzoneDB: failed to open database");
    return;
  }
  db_instance->setEventListener(new EventListenerOzonedb());
  OZONEDB_JNI_CATCH(env, )
}

JNIEXPORT void JNICALL Java_jni_OzoneDBJNI_closeDB(JNIEnv* env, jobject obj) {
  OZONEDB_JNI_TRY
  ozonedb::Status status = ozonedb::DB::closeDB(db_instance);
  if (status != ozonedb::Status::kSuccess) {
    std::cerr << "Failed to close database" << std::endl;
  }
  OZONEDB_JNI_CATCH(env, )
}

// Returns true when the write reached the log. A put that returns false
// was never appended; the client must not count it as a completed operation.
JNIEXPORT jboolean JNICALL Java_jni_OzoneDBJNI_put(JNIEnv* env, jobject obj, jstring key, jbyteArray value) {
  OZONEDB_JNI_TRY
  char const* nativeKey = env->GetStringUTFChars(key, 0);
  jsize length = env->GetArrayLength(value);
  jbyte *byteArrayPtr = env->GetByteArrayElements(value, nullptr);
  std::string str(reinterpret_cast<char*>(byteArrayPtr), length);
  ozonedb::Status status = db_instance->put(std::string(nativeKey), str);
  env->ReleaseStringUTFChars(key, nativeKey);
   env->ReleaseByteArrayElements(value, byteArrayPtr, 0);

  if (status != ozonedb::Status::kSuccess) {
    std::cerr << "Failed to put key-value pair" << std::endl;
    return JNI_FALSE;
  }
  return JNI_TRUE;
  OZONEDB_JNI_CATCH(env, JNI_FALSE)
}

JNIEXPORT jbyteArray JNICALL Java_jni_OzoneDBJNI_get(JNIEnv* env, jobject obj, jstring key) {
  OZONEDB_JNI_TRY
  char const* nativeKey = env->GetStringUTFChars(key, 0);
  std::string const* value;
  // value aliases bytes owned by guard; keep guard alive through the
  // byte-array copy below.
  std::shared_ptr<Record> guard;
  ozonedb::Status status = db_instance->get(std::string(nativeKey), value, guard);
  env->ReleaseStringUTFChars(key, nativeKey);

  if (status != ozonedb::Status::kSuccess) {
    // A miss is normal; the Java side turns null into NOT_FOUND.
    return nullptr;
  }
  jbyteArray byteArray = env->NewByteArray(value->length());
  env->SetByteArrayRegion(byteArray, 0, value->length(), reinterpret_cast<const jbyte*>(value->c_str()));
  return byteArray;
  OZONEDB_JNI_CATCH(env, nullptr)
}

// Batch fence for strict reads: one storage fence on the calling thread,
// reused by every get() until clearSync() (see DB::sync).
// db_instance is thread_local, so the fence token and the DB share one thread.
JNIEXPORT void JNICALL Java_jni_OzoneDBJNI_sync(JNIEnv* env, jobject obj) {
  OZONEDB_JNI_TRY
  db_instance->sync();
  OZONEDB_JNI_CATCH(env, )
}

JNIEXPORT void JNICALL Java_jni_OzoneDBJNI_clearSync(JNIEnv* env, jobject obj) {
  OZONEDB_JNI_TRY
  db_instance->clearSync();
  OZONEDB_JNI_CATCH(env, )
}

JNIEXPORT jboolean JNICALL Java_jni_OzoneDBJNI_remove(JNIEnv* env, jobject obj, jstring key) {
  OZONEDB_JNI_TRY
  char const* nativeKey = env->GetStringUTFChars(key, 0);
  ozonedb::Status status = db_instance->remove(std::string(nativeKey));
  env->ReleaseStringUTFChars(key, nativeKey);

  if (status != ozonedb::Status::kSuccess) {
    std::cerr << "Failed to remove key" << std::endl;
    return JNI_FALSE;
  }
  return JNI_TRUE;
  OZONEDB_JNI_CATCH(env, JNI_FALSE)
}
