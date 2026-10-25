#ifndef OZONEDB_CORFU_CLIENT_H
#define OZONEDB_CORFU_CLIENT_H
#ifdef OZONEDB_ENABLE_CORFU
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <string_view>

namespace ozonedb {

// Settings CorfuDBStorage needs to reach one Corfu stream. DB::DB fills
// them from the corfu_* config keys; tests fill them directly.
struct CorfuClientOptions {
  std::string endpoint;      // "host:port" of the single Corfu node
  std::string stream_name;   // the OzoneDB stream (corfu_stream_name)
  // Defaults match the Java runtime.
  int read_batch = 1000;             // addresses per ReadLogRequest
  int idle_poll_ms = 5;              // wait between stream-tail queries
  int hole_fill_timeout_ms = 10000;  // EMPTY address -> HOLE write
  int request_timeout_ms = 5000;     // per RPC
};

/**
 * @brief The operations CorfuDBStorage uses to drive one Corfu stream.
 *
 * One instance is one client id on one stream. Any thread may call any
 * method at any time. Implementations serialize internally where needed.
 *
 * Addresses are global Corfu log addresses. A negative address means
 * "not appended" or "unknown". The caller must never use it as a log position.
 *
 * pollBatch delivers entries in address order, once per address per seek
 * position. It never skips a trimmed address in silence: it reports
 * kTrimmed, and CorfuDBStorage restarts from a newer checkpoint or fail-stops.
 */
class CorfuClient {
 public:
  // Abort codes of appendChecked. All negative, so none is a log address.
  static constexpr int64_t kAbortConflict = -2;
  static constexpr int64_t kAbortNewSequencer = -3;
  static constexpr int64_t kAbortSeqOverflow = -4;
  static constexpr int64_t kAbortSeqTrim = -5;
  static constexpr int64_t kAbortOther = -6;

  // Outcome of appendChecked. addr >= 0 on success. Otherwise abort holds
  // a kAbort* code, and offending holds the conflicting address for
  // kAbortConflict (-1 for the other codes). A refused append has no address.
  struct CheckedAppend {
    int64_t addr = -1;
    int64_t abort = 0;
    int64_t offending = -1;
  };

  // Result of one pollBatch call.
  //   kEntries: the sink received at least one entry.
  //   kIdle:    nothing new before the timeout; the sink was not called.
  //   kTrimmed: the next address is below the trim mark. Nothing was
  //             delivered; the cursor did not move.
  //   kError:   the client failed, for example a dead connection.
  //             Entries delivered before the failure stay delivered.
  enum class Poll { kEntries,
                    kIdle,
                    kTrimmed,
                    kError };

  // Receives one entry: its global address and the raw CorfuEntry bytes
  // the writer appended. `data` is valid only during the call.
  using EntrySink = std::function<void(int64_t addr, unsigned char const* data, size_t len)>;

  virtual ~CorfuClient() = default;

  // Plain append: a token without conflict keys, then the write. Returns
  // the address, or -1 when nothing was appended.
  virtual int64_t append(std::string_view payload) = 0;

  // Append whose token request carries conflict keys. The sequencer then
  // checks at token time whether read_key was written above `snapshot`.
  // read_key goes into the read set at `snapshot`; write_key goes into
  // the write set at the new address. Either may be null. The keys are
  // raw bytes, not hashed, the same bytes a Java writer uses.
  virtual CheckedAppend appendChecked(std::string_view payload, int64_t snapshot,
                                      std::string_view const* read_key,
                                      std::string_view const* write_key) = 0;

  // The global log tail (last issued address), or -1 on error. One
  // sequencer round trip.
  virtual int64_t globalTail() = 0;

  // The tail of this stream, not the global tail: the highest address
  // the tailer can deliver. -6 (Address.NON_EXIST) when the stream is
  // empty, -1 on error.
  virtual int64_t streamTail() = 0;

  // Deliver up to max_entries entries at and above the cursor. Returns
  // as soon as it has one entry and the next is not ready; it never pads
  // a batch. An own append through this client wakes a waiting poll.
  virtual Poll pollBatch(int timeout_ms, int max_entries, EntrySink const& sink) = 0;

  // Move the cursor so that the next pollBatch delivers the first entry
  // of the stream at or above addr. No RPC. False on error.
  virtual bool seek(int64_t addr) = 0;

  // Hint that every address below mark was applied; the client may drop
  // its bookkeeping for them. No-op for a client that keeps none.
  virtual void gc(int64_t mark) = 0;

  // Mark every address <= addr trimmed on the sequencer and the log
  // unit, compact, and return the log unit's trim mark (first untrimmed
  // address). -1 on error.
  virtual int64_t prefixTrim(int64_t addr) = 0;

  // The first untrimmed address as persisted by the log unit (0 or -1 on
  // a log that was never trimmed). -1 on error.
  virtual int64_t trimMark() = 0;

  // Stop the client. Every later call fails. Idempotent.
  virtual void close() = 0;
};

// The C++ client (src/db/corfu/native_corfu_client.cpp). Throws
// std::runtime_error when the client cannot connect.
std::unique_ptr<CorfuClient> makeCorfuClient(CorfuClientOptions const& options);

}  // namespace ozonedb
#endif  // OZONEDB_ENABLE_CORFU
#endif  // OZONEDB_CORFU_CLIENT_H
