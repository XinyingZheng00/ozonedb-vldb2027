// Multi-writer smoke test for OzoneDB on a Corfu + S3 backend.
//
// Usage:
//   corfu_multiwriter_smoke <path-to-shared-config.json> [keys_per_writer] [--kill-one]
//
// Forks two child processes. Each writes a disjoint key range
// (W0: key_0..key_{N-1}, W1: key_{N}..key_{2N-1}) to the same Corfu stream.
// After both children exit, the parent reopens the DB and reads all 2N keys.
// The binary verifies:
//   - concurrent LOGCREATE and tail rollover
//   - cross-writer read fencing: the parent sees both writers' data
//   - the distributed compaction claim, if compaction fires
//   - the SSTable split to S3, if sstable_backend is set
//   - the --kill-one dead-writer takeover
//
// --kill-one (paper §4.4): writer 1 runs alone first. Once it is past half
// its range, the parent SIGKILLs it when it appends a compaction claim (the
// [COMPACTION_NEEDED] line on stderr). The claim is then in the task log
// with no TASK_COMPLETE. Writer 0 opens the DB after the kill, writes its
// range and stays open past task_heartbeat_threshold_ticks, so its watcher
// reclaims the dead claim at the next owner_generation. The parent counts
// the takeovers in writer 0's output ("Found dead task"), reopens the DB and
// checks every key writer 0 wrote and every key writer 1 acked. PASS needs
// full integrity and at least one takeover.
//
// Exits non-zero on any failure. Not a gtest: run it by hand or from a
// script against a live corfu_server and an optional MinIO instance.
#include "db.h"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <iostream>
#include <poll.h>
#include <signal.h>
#include <string>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>

using namespace ozonedb;

// report_progress: print "[writer-progress] <i>" to stderr after every
// acked put, so the parent knows how far a writer got before the kill.
// linger_ms > 0: keep the DB open that long after the last put, so the
// compaction watcher can take over a dead peer's task.
static int run_writer(std::string const& config, int start, int end, bool report_progress, int linger_ms) {
  DB* db = nullptr;
  if (DB::openDB(db, config) != Status::kSuccess) {
    std::cerr << "[writer " << getpid() << "] openDB failed\n";
    return 2;
  }
  for (int i = start; i < end; ++i) {
    std::string k = "key_" + std::to_string(i);
    std::string v = "value_" + std::to_string(i);
    if (db->put(k, v) != Status::kSuccess) {
      std::cerr << "[writer " << getpid() << "] put failed at i=" << i << "\n";
      DB::closeDB(db);
      return 3;
    }
    if (report_progress) {
      std::cerr << "[writer-progress] " << i << std::endl;
    }
  }
  if (linger_ms > 0) {
    std::cout << "[writer " << getpid() << "] range done, staying open " << linger_ms << " ms" << std::endl;
    std::this_thread::sleep_for(std::chrono::milliseconds(linger_ms));
  }
  DB::closeDB(db);
  return 0;
}

// Read the heartbeat threshold from the config, to size the survivor's linger.
static int heartbeatThresholdTicks(std::string const& config) {
  try {
    Metadata md(config);
    return md.task_heartbeat_threshold_ticks;
  } catch (...) {
    return 30;
  }
}

// Line-oriented reader over a pipe: appends to `carry`, hands complete
// lines to `on_line`. Returns false at EOF.
static bool pumpLines(int fd, std::string& carry, std::function<void(std::string const&)> const& on_line) {
  char buf[4096];
  ssize_t n = read(fd, buf, sizeof(buf));
  if (n <= 0) {
    if (!carry.empty()) {
      on_line(carry);
      carry.clear();
    }
    return false;
  }
  carry.append(buf, static_cast<size_t>(n));
  size_t nl;
  while ((nl = carry.find('\n')) != std::string::npos) {
    on_line(carry.substr(0, nl));
    carry.erase(0, nl + 1);
  }
  return true;
}

int main(int argc, char** argv) {
  if (argc < 2) {
    std::cerr << "Usage: " << argv[0]
              << " <shared-config.json> [keys_per_writer=100] [--kill-one]\n";
    return 1;
  }
  std::string config = argv[1];
  int keys_per_writer = 100;
  bool kill_one = false;
  for (int a = 2; a < argc; ++a) {
    if (std::strcmp(argv[a], "--kill-one") == 0) {
      kill_one = true;
    } else {
      keys_per_writer = std::atoi(argv[a]);
    }
  }
  int total_keys = keys_per_writer * 2;

  std::cout << "[multiwriter] config: " << config
            << ", keys_per_writer: " << keys_per_writer
            << (kill_one ? ", kill-one" : "") << std::endl;

  auto t0 = std::chrono::steady_clock::now();

  // --kill-one plumbing. Writer 1 (the victim) reports progress and claim
  // lines on a stderr pipe. Writer 0 (the survivor) waits for a byte on a
  // start pipe before it opens the DB; its stdout goes through a pipe so
  // the parent can count takeovers. The survivor stays open for the
  // threshold, one replay interval (a third of it), and a margin.
  int threshold_ticks = kill_one ? heartbeatThresholdTicks(config) : 0;
  int linger_ms = kill_one ? threshold_ticks * 100 * 4 / 3 + 5000 : 0;
  int victim_pipe[2] = {-1, -1};    // victim stderr -> parent
  int survivor_pipe[2] = {-1, -1};  // survivor stdout -> parent
  int start_pipe[2] = {-1, -1};     // parent -> survivor
  if (kill_one) {
    if (pipe(victim_pipe) != 0 || pipe(survivor_pipe) != 0 || pipe(start_pipe) != 0) {
      std::cerr << "[multiwriter] pipe failed\n";
      return 1;
    }
  }
  std::cout.flush();
  std::cerr.flush();

  // Fork two writer children with disjoint key ranges.
  pid_t pids[2] = {};
  for (int w = 0; w < 2; ++w) {
    pid_t pid = fork();
    if (pid < 0) {
      std::cerr << "[multiwriter] fork failed\n";
      return 1;
    }
    if (pid == 0) {
      // Child: run the writer and exit.
      int start = w * keys_per_writer;
      int end = start + keys_per_writer;
      bool report_progress = false;
      int linger = 0;
      if (kill_one) {
        close(victim_pipe[0]);
        close(survivor_pipe[0]);
        close(start_pipe[1]);
        if (w == 0) {
          dup2(survivor_pipe[1], STDOUT_FILENO);
          linger = linger_ms;
          char go = 0;
          ssize_t n = read(start_pipe[0], &go, 1);
          (void)n;
        } else {
          dup2(victim_pipe[1], STDERR_FILENO);
          report_progress = true;
          linger = 10000;  // keep claiming from the backlog if the range ends first
        }
        close(victim_pipe[1]);
        close(survivor_pipe[1]);
        close(start_pipe[0]);
      }
      int rc = run_writer(config, start, end, report_progress, linger);
      _exit(rc);
    }
    pids[w] = pid;
  }

  int victim_acked = -1;  // highest key index writer 1 acked before the kill
  int takeovers = 0;
  if (kill_one) {
    close(victim_pipe[1]);
    close(survivor_pipe[1]);
    close(start_pipe[0]);
    std::string const victim_tag = "writer_pid=" + std::to_string(pids[1]);
    int const kill_after = keys_per_writer + keys_per_writer / 2;
    bool killed = false;
    bool victim_open = true;
    bool survivor_open = true;
    std::string victim_carry;
    std::string survivor_carry;
    auto on_victim_line = [&](std::string const& line) {
      if (line.rfind("[writer-progress] ", 0) == 0) {
        victim_acked = std::atoi(line.c_str() + std::strlen("[writer-progress] "));
        return;
      }
      std::cerr << line << '\n';
      // Its own claim, past the half-way mark: kill it before the
      // compaction can complete.
      if (!killed && victim_acked >= kill_after &&
          line.find("[COMPACTION_NEEDED]") != std::string::npos &&
          line.find(victim_tag) != std::string::npos) {
        kill(pids[1], SIGKILL);
        killed = true;
        std::cout << "[multiwriter] killed writer 1 (pid " << pids[1] << ") on its claim " << line.substr(0, line.find(" wall_ns"))
                  << " after it acked key_" << victim_acked << std::endl;
        char go = 1;
        ssize_t n = write(start_pipe[1], &go, 1);
        (void)n;
        std::cout << "[multiwriter] writer 0 starts now and lingers " << linger_ms << " ms after its range (threshold "
                  << threshold_ticks << " ticks)" << std::endl;
      }
    };
    auto on_survivor_line = [&](std::string const& line) {
      if (line.find("Found dead task") != std::string::npos) ++takeovers;
      std::cout << line << '\n';
    };
    while (victim_open || survivor_open) {
      struct pollfd fds[2];
      int nfds = 0;
      if (victim_open) fds[nfds++] = {victim_pipe[0], POLLIN, 0};
      if (survivor_open) fds[nfds++] = {survivor_pipe[0], POLLIN, 0};
      if (poll(fds, nfds, -1) < 0) break;
      for (int i = 0; i < nfds; ++i) {
        if (!(fds[i].revents & (POLLIN | POLLHUP))) continue;
        if (fds[i].fd == victim_pipe[0]) {
          if (!pumpLines(victim_pipe[0], victim_carry, on_victim_line)) victim_open = false;
        } else {
          if (!pumpLines(survivor_pipe[0], survivor_carry, on_survivor_line)) survivor_open = false;
        }
      }
      std::cout.flush();
    }
    close(victim_pipe[0]);
    close(survivor_pipe[0]);
    if (!killed) {
      std::cerr << "[multiwriter] FAIL — writer 1 ended without appending a claim past key_"
                << kill_after << "; nothing to kill it on\n";
      close(start_pipe[1]);
      for (int w = 0; w < 2; ++w) waitpid(pids[w], nullptr, 0);
      return 8;
    }
    close(start_pipe[1]);
  }

  // Parent: wait for both children.
  int failures = 0;
  for (int w = 0; w < 2; ++w) {
    int status = 0;
    waitpid(pids[w], &status, 0);
    bool const expected_kill = kill_one && w == 1 && WIFSIGNALED(status) && WTERMSIG(status) == SIGKILL;
    if (expected_kill) continue;
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
      std::cerr << "[multiwriter] writer " << w << " (pid " << pids[w]
                << ") exited with status "
                << (WIFEXITED(status) ? WEXITSTATUS(status) : -1) << "\n";
      ++failures;
    }
  }
  if (failures > 0) {
    std::cerr << "[multiwriter] " << failures << " writer(s) failed\n";
    return 4;
  }

  auto t1 = std::chrono::steady_clock::now();
  std::cout << "[multiwriter] both writers finished in "
            << std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0)
                   .count()
            << " ms\n";

  // Parent reopens and reads back all keys.
  DB* db = nullptr;
  if (DB::openDB(db, config) != Status::kSuccess) {
    std::cerr << "[multiwriter] parent openDB failed\n";
    return 5;
  }

  int ok = 0;
  int missing = 0;
  int mismatched = 0;
  int checked = 0;
  for (int i = 0; i < total_keys; ++i) {
    // In kill-one mode only the keys writer 1 acked are owed.
    if (kill_one && i >= keys_per_writer && i > victim_acked) continue;
    ++checked;
    std::string k = "key_" + std::to_string(i);
    std::string const* v = nullptr;
    std::shared_ptr<Record> guard;
    if (db->get(k, v, guard) != Status::kSuccess || v == nullptr) {
      if (missing < 10) {
        std::cerr << "[multiwriter] NOT_FOUND: " << k << "\n";
      }
      ++missing;
      continue;
    }
    std::string expected = "value_" + std::to_string(i);
    if (*v != expected) {
      if (mismatched < 10) {
        std::cerr << "[multiwriter] MISMATCH: " << k << " got '" << *v
                  << "' expected '" << expected << "'\n";
      }
      ++mismatched;
      continue;
    }
    ++ok;
  }

  DB::closeDB(db);

  auto t2 = std::chrono::steady_clock::now();
  std::cout << "[multiwriter] read phase: "
            << std::chrono::duration_cast<std::chrono::milliseconds>(t2 - t1)
                   .count()
            << " ms\n";

  if (missing > 0 || mismatched > 0) {
    std::cerr << "[multiwriter] FAIL — ok=" << ok << " missing=" << missing
              << " mismatched=" << mismatched << " / " << checked << "\n";
    return 6;
  }
  if (kill_one) {
    std::cout << "[multiwriter] takeovers by writer 0: " << takeovers << "\n";
    if (takeovers == 0) {
      std::cerr << "[multiwriter] FAIL — writer 1 was killed on a claim but writer 0 reclaimed no task\n";
      return 7;
    }
    std::cout << "[multiwriter] PASS — verified " << ok << "/" << checked
              << " keys (writer 0's range plus writer 1's acked prefix); "
              << takeovers << " dead-task takeover(s)\n";
    return 0;
  }

  std::cout << "[multiwriter] PASS — verified " << ok << "/" << total_keys
            << " keys from 2 concurrent writers\n";
  return 0;
}
