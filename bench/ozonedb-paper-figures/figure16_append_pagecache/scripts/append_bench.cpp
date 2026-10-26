// append_bench: N processes append fixed-size records to ONE shared file for a
// fixed duration. The file is open with O_APPEND (POSIX atomic append). The
// program reports the aggregate append throughput (paper section 5.6, Figure 16).
//
//   --mode buffered   writes go through the OS page cache
//   --mode direct     O_DIRECT, bypasses the page cache (Linux). macOS has no
//                     O_DIRECT. There the program uses F_NOCACHE instead.
//   --sync fsync      fsync() after every append (durable append)
//   --sync none       no fsync
//
// Every record starts with (magic, writer id, sequence number). With --verify,
// the program re-reads the file after the run. It checks that the file size
// matches the counted appends, and that no record was torn or interleaved.
//
// Output: one CSV line on stdout:
//   mode,sync,procs,record_bytes,seconds,total_ops,ops_per_sec,verified
//
// Build:  c++ -O2 -std=c++17 -o append_bench append_bench.cpp
// Run:    ./append_bench --path /mnt/ssd/appendtest/append.dat --mode buffered \
//                        --sync fsync --procs 8 --duration 10 --verify
#include <fcntl.h>
#include <getopt.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>
#include <string>
#include <thread>
#include <vector>

namespace {

constexpr uint32_t kMagic = 0x4F5A4150;  // "OZAP"
constexpr size_t kAlign = 4096;          // O_DIRECT buffer alignment
constexpr int kMaxProcs = 1024;

struct RecordHeader {
  uint32_t magic;
  uint32_t writer;
  uint64_t seq;
};

// This struct lives in an anonymous MAP_SHARED mapping. Every forked writer can see it.
struct Shared {
  std::atomic<bool> start;
  std::atomic<bool> stop;
  std::atomic<uint64_t> ops[kMaxProcs];
  std::atomic<int> error[kMaxProcs];  // errno of a failed writer, 0 if fine
};

struct Options {
  std::string path;
  bool direct = false;
  bool fsync_each = true;
  int procs = 1;
  double duration = 10;
  size_t record = 4096;
  bool verify = false;
};

char FillByte(int writer) { return static_cast<char>('a' + writer % 26); }

int OpenShared(const std::string& path, bool direct) {
  int flags = O_WRONLY | O_APPEND;
#ifdef O_DIRECT
  if (direct) flags |= O_DIRECT;
#endif
  int fd = open(path.c_str(), flags);
#if !defined(O_DIRECT) && defined(F_NOCACHE)
  if (fd >= 0 && direct && fcntl(fd, F_NOCACHE, 1) < 0) return -1;
#endif
  return fd;
}

[[noreturn]] void Writer(Shared* sh, int id, const Options& opt) {
  int fd = OpenShared(opt.path, opt.direct);
  void* mem = nullptr;
  if (fd < 0 || posix_memalign(&mem, kAlign, opt.record) != 0) {
    sh->error[id] = errno ? errno : ENOMEM;
    _exit(1);
  }
  auto* buf = static_cast<char*>(mem);
  std::memset(buf, FillByte(id), opt.record);
  auto* hdr = reinterpret_cast<RecordHeader*>(buf);
  hdr->magic = kMagic;
  hdr->writer = static_cast<uint32_t>(id);

  while (!sh->start.load(std::memory_order_acquire)) {
  }
  uint64_t n = 0;
  while (!sh->stop.load(std::memory_order_relaxed)) {
    hdr->seq = n;
    if (write(fd, buf, opt.record) != static_cast<ssize_t>(opt.record)) {
      sh->error[id] = errno ? errno : EIO;
      break;
    }
    if (opt.fsync_each && fsync(fd) != 0) {
      sh->error[id] = errno;
      break;
    }
    sh->ops[id].store(++n, std::memory_order_relaxed);
  }
  close(fd);
  _exit(sh->error[id] ? 1 : 0);
}

// Every record must carry MAGIC, a valid writer id, that writer's next
// sequence number and its fill byte. The record count must equal `total`.
bool Verify(const Options& opt, uint64_t total) {
  int fd = open(opt.path.c_str(), O_RDONLY);
  struct stat st {};
  if (fd < 0 || fstat(fd, &st) != 0 ||
      static_cast<uint64_t>(st.st_size) != total * opt.record) {
    std::fprintf(stderr, "verify: size %lld, expected %llu\n", static_cast<long long>(st.st_size),
                 static_cast<unsigned long long>(total * opt.record));
    if (fd >= 0) close(fd);
    return false;
  }
  std::vector<uint64_t> next(opt.procs, 0);
  std::vector<char> buf(opt.record);
  for (uint64_t i = 0; i < total; i++) {
    if (read(fd, buf.data(), opt.record) != static_cast<ssize_t>(opt.record)) {
      close(fd);
      return false;
    }
    RecordHeader hdr;
    std::memcpy(&hdr, buf.data(), sizeof hdr);
    if (hdr.magic != kMagic || hdr.writer >= static_cast<uint32_t>(opt.procs) ||
        hdr.seq != next[hdr.writer]++ || buf.back() != FillByte(hdr.writer)) {
      std::fprintf(stderr, "verify: bad record %llu\n", static_cast<unsigned long long>(i));
      close(fd);
      return false;
    }
  }
  close(fd);
  return true;
}

void Usage(const char* prog) {
  std::fprintf(stderr,
               "usage: %s --path FILE [--mode buffered|direct] [--sync fsync|none]\n"
               "          [--procs N] [--duration S] [--record BYTES] [--verify]\n",
               prog);
}

bool ParseArgs(int argc, char** argv, Options* opt) {
  static const option kLong[] = {
      {"path", required_argument, nullptr, 'p'},  {"mode", required_argument, nullptr, 'm'},
      {"sync", required_argument, nullptr, 's'},  {"procs", required_argument, nullptr, 'n'},
      {"duration", required_argument, nullptr, 'd'}, {"record", required_argument, nullptr, 'r'},
      {"verify", no_argument, nullptr, 'v'},       {nullptr, 0, nullptr, 0}};
  std::string mode = "buffered", sync = "fsync";
  for (int c; (c = getopt_long(argc, argv, "", kLong, nullptr)) != -1;) {
    switch (c) {
      case 'p': opt->path = optarg; break;
      case 'm': mode = optarg; break;
      case 's': sync = optarg; break;
      case 'n': opt->procs = std::atoi(optarg); break;
      case 'd': opt->duration = std::atof(optarg); break;
      case 'r': opt->record = std::strtoul(optarg, nullptr, 10); break;
      case 'v': opt->verify = true; break;
      default: return false;
    }
  }
  if ((mode != "buffered" && mode != "direct") || (sync != "fsync" && sync != "none")) return false;
  opt->direct = mode == "direct";
  opt->fsync_each = sync == "fsync";
  return !opt->path.empty() && opt->procs >= 1 && opt->procs <= kMaxProcs &&
         opt->record >= sizeof(RecordHeader) && opt->record % 512 == 0;
}

}  // namespace

int main(int argc, char** argv) {
  Options opt;
  if (!ParseArgs(argc, argv, &opt)) {
    Usage(argv[0]);
    return 2;
  }

  // Fresh, empty file for every run.
  int fd = open(opt.path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (fd < 0) {
    std::perror("create");
    return 1;
  }
  fsync(fd);
  close(fd);

  void* mem = mmap(nullptr, sizeof(Shared), PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0);
  if (mem == MAP_FAILED) {
    std::perror("mmap");
    return 1;
  }
  auto* sh = new (mem) Shared{};

  std::vector<pid_t> pids;
  for (int i = 0; i < opt.procs; i++) {
    pid_t pid = fork();
    if (pid < 0) {
      std::perror("fork");
      return 1;
    }
    if (pid == 0) Writer(sh, i, opt);
    pids.push_back(pid);
  }

  using Clock = std::chrono::steady_clock;
  std::this_thread::sleep_for(std::chrono::milliseconds(200));  // let writers open and spin
  auto t0 = Clock::now();
  sh->start.store(true, std::memory_order_release);
  std::this_thread::sleep_for(std::chrono::duration<double>(opt.duration));
  sh->stop.store(true, std::memory_order_relaxed);
  double elapsed = std::chrono::duration<double>(Clock::now() - t0).count();

  bool failed = false;
  for (int i = 0; i < opt.procs; i++) {
    int status;
    waitpid(pids[i], &status, 0);
    if (int err = sh->error[i]) {
      std::fprintf(stderr, "writer %d: %s\n", i, std::strerror(err));
      failed = true;
    }
  }
  if (failed) return 1;

  uint64_t total = 0;
  for (int i = 0; i < opt.procs; i++) total += sh->ops[i];
  const char* verified = opt.verify ? (Verify(opt, total) ? "ok" : "FAILED") : "skipped";

  std::printf("%s,%s,%d,%zu,%.3f,%llu,%.1f,%s\n", opt.direct ? "direct" : "buffered",
              opt.fsync_each ? "fsync" : "none", opt.procs, opt.record, elapsed,
              static_cast<unsigned long long>(total), total / elapsed, verified);
  return std::strcmp(verified, "FAILED") == 0 ? 1 : 0;
}
