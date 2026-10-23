// latency_probe: single-writer per-operation latencies, used to sanity-check
// what limits each Figure 16 curve. Every probe writes 4 KiB records and
// reports mean / p50 / p99 per step in microseconds.
//
//   append-fsync          buffered O_APPEND write, then fsync      (fsync · Buffered, N=1)
//   direct-append-fsync   O_DIRECT O_APPEND write, then fsync      (fsync · O_DIRECT, N=1)
//   direct-append         O_DIRECT O_APPEND write                  (no-sync · O_DIRECT)
//   direct-overwrite      O_DIRECT pwrite into preallocated space  (same write, no file growth)
//   device-flush          O_DIRECT overwrite, then fdatasync: no metadata changes, so the
//                         fdatasync is only the device cache flush
//
// Output: CSV lines  probe,step,ops,mean_us,p50_us,p99_us
//
// Build:  c++ -O2 -std=c++17 -o latency_probe latency_probe.cpp
// Run:    ./latency_probe --path /mnt/ssd/appendtest/probe.dat --ops 5000
#include <fcntl.h>
#include <getopt.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace {

constexpr size_t kRecord = 4096;
using Clock = std::chrono::steady_clock;

double Us(Clock::time_point a, Clock::time_point b) {
  return std::chrono::duration<double, std::micro>(b - a).count();
}

void Report(const std::string& probe, const std::string& step, std::vector<double> v) {
  if (v.empty()) return;
  std::sort(v.begin(), v.end());
  double mean = 0;
  for (double x : v) mean += x;
  mean /= v.size();
  std::printf("%s,%s,%zu,%.1f,%.1f,%.1f\n", probe.c_str(), step.c_str(), v.size(), mean,
              v[v.size() / 2], v[v.size() * 99 / 100]);
}

int Open(const std::string& path, int flags, bool direct) {
#ifdef O_DIRECT
  if (direct) flags |= O_DIRECT;
#endif
  int fd = open(path.c_str(), flags, 0644);
  if (fd < 0) {
    std::perror(path.c_str());
    std::exit(1);
  }
#if !defined(O_DIRECT) && defined(F_NOCACHE)
  if (direct) fcntl(fd, F_NOCACHE, 1);
#endif
  return fd;
}

int DataSync(int fd) {
#ifdef __APPLE__
  return fsync(fd);
#else
  return fdatasync(fd);
#endif
}

void Check(bool ok, const char* what) {
  if (!ok) {
    std::perror(what);
    std::exit(1);
  }
}

// Fresh empty file; returns an fd opened with O_APPEND.
int FreshAppend(const std::string& path, bool direct) {
  close(Open(path, O_WRONLY | O_CREAT | O_TRUNC, false));
  return Open(path, O_WRONLY | O_APPEND, direct);
}

// File of `ops` records, written and synced, so overwrites change no metadata.
int Preallocated(const std::string& path, int ops, const char* buf) {
  int fd = Open(path, O_WRONLY | O_CREAT | O_TRUNC, false);
  for (int i = 0; i < ops; i++) Check(write(fd, buf, kRecord) == (ssize_t)kRecord, "prealloc");
  Check(fsync(fd) == 0, "prealloc fsync");
  close(fd);
  return Open(path, O_WRONLY, true);
}

void AppendThenSync(const std::string& probe, const std::string& path, bool direct, int ops,
                    const char* buf) {
  int fd = FreshAppend(path, direct);
  std::vector<double> w, s, total;
  for (int i = 0; i < ops; i++) {
    auto t0 = Clock::now();
    Check(write(fd, buf, kRecord) == (ssize_t)kRecord, "write");
    auto t1 = Clock::now();
    Check(fsync(fd) == 0, "fsync");
    auto t2 = Clock::now();
    w.push_back(Us(t0, t1));
    s.push_back(Us(t1, t2));
    total.push_back(Us(t0, t2));
  }
  close(fd);
  Report(probe, "write", w);
  Report(probe, "fsync", s);
  Report(probe, "total", total);
}

}  // namespace

int main(int argc, char** argv) {
  std::string path;
  int ops = 5000;
  static const option kLong[] = {{"path", required_argument, nullptr, 'p'},
                                 {"ops", required_argument, nullptr, 'n'},
                                 {nullptr, 0, nullptr, 0}};
  for (int c; (c = getopt_long(argc, argv, "", kLong, nullptr)) != -1;) {
    if (c == 'p') path = optarg;
    else if (c == 'n') ops = std::atoi(optarg);
    else return 2;
  }
  if (path.empty() || ops < 1) {
    std::fprintf(stderr, "usage: %s --path FILE [--ops N]\n", argv[0]);
    return 2;
  }

  void* mem = nullptr;
  Check(posix_memalign(&mem, 4096, kRecord) == 0, "posix_memalign");
  auto* buf = static_cast<char*>(mem);
  std::memset(buf, 'x', kRecord);
  std::printf("probe,step,ops,mean_us,p50_us,p99_us\n");

  AppendThenSync("append-fsync", path, false, ops, buf);
  AppendThenSync("direct-append-fsync", path, true, ops, buf);

  {
    int fd = FreshAppend(path, true);
    std::vector<double> w;
    for (int i = 0; i < ops; i++) {
      auto t0 = Clock::now();
      Check(write(fd, buf, kRecord) == (ssize_t)kRecord, "write");
      w.push_back(Us(t0, Clock::now()));
    }
    close(fd);
    Report("direct-append", "write", w);
  }
  {
    int fd = Preallocated(path, ops, buf);
    std::vector<double> w, f;
    for (int i = 0; i < ops; i++) {
      auto t0 = Clock::now();
      Check(pwrite(fd, buf, kRecord, (off_t)i * kRecord) == (ssize_t)kRecord, "pwrite");
      auto t1 = Clock::now();
      w.push_back(Us(t0, t1));
    }
    Report("direct-overwrite", "write", w);
    for (int i = 0; i < ops; i++) {
      Check(pwrite(fd, buf, kRecord, (off_t)i * kRecord) == (ssize_t)kRecord, "pwrite");
      auto t0 = Clock::now();
      Check(DataSync(fd) == 0, "fdatasync");
      f.push_back(Us(t0, Clock::now()));
    }
    close(fd);
    Report("device-flush", "fdatasync", f);
  }
  unlink(path.c_str());
  return 0;
}
