#include "helper.h"
#include <algorithm>
#include <functional>
#include <regex>
#include <sstream>

// Prefix of a file name of the form prefix/suffix; "" if no match.
std::string getPrefix(std::string const& filename) {
  std::regex re(R"(^([^/]+)/[^/]+$)");
  std::smatch match;
  if (std::regex_search(filename, match, re)) {
    return match[1];
  }
  return "";
}

// Suffix of a file name of the form prefix/suffix; "" if no match.
std::string getSuffix(std::string const& filename) {
  std::regex re(R"(^[^/]+/([^/]+)$)");
  std::smatch match;
  if (std::regex_search(filename, match, re)) {
    return match[1];
  }
  return "";
}

int getNumberBeforeUnderscore(std::string const& filename) {
  // Digits of the suffix before its first underscore; -1 if none
  std::regex re(R"([^/]+/([0-9]+)_[^/]+$)");
  std::smatch match;
  if (std::regex_search(filename, match, re)) {
    return std::stoi(match[1]);
  }
  return -1;
}

int getNumberInTheEnd(std::string const& filename) {
  std::regex re(R"(.*(\d+)$)");
  std::smatch match;
  if (std::regex_search(filename, match, re)) {
    return std::stoi(match[1]);
  }
  return -1;
}

// Level number of an SSTable name of the form sstable<level>/<fingerprint><timestamp>.sst; -1 if none
int getSSTLayerNumber(std::string const& filename) {
  std::regex re(R"(sstable(\d+)/.*)");
  std::smatch match;
  if (std::regex_search(filename, match, re)) {
    return std::stoi(match[1]);
  }
  return -1;
}

#include <boost/uuid/uuid.hpp>
#include <boost/uuid/uuid_generators.hpp>
#include <boost/uuid/uuid_io.hpp>
#include <chrono>
#include <iomanip>
#include <sstream>

// Generate a per-process fingerprint: a random UUID followed by a local timestamp.
std::string generateFingerprint() {
  boost::uuids::random_generator generator;
  boost::uuids::uuid uuid = generator();

  auto now = std::chrono::system_clock::now();
  auto time_t_now = std::chrono::system_clock::to_time_t(now);
  std::stringstream ss;
  ss << std::put_time(std::localtime(&time_t_now), "%Y%m%d%H%M%S");
  std::string timestamp = ss.str();

  std::stringstream fingerprint;
  fingerprint << uuid << timestamp;
  return fingerprint.str();
}

std::string formatTaskId(std::vector<std::string> const& input_files,
                         int dest_level, bool in_last_level) {
  // Input file names are globally unique. Sort them, so peers that see
  // input_files in a different order hash to the same value.
  std::vector<std::string> sorted = input_files;
  std::sort(sorted.begin(), sorted.end());
  std::string joined;
  for (auto const& f : sorted) {
    joined += f;
    joined.push_back(',');
  }
  std::ostringstream os;
  os << std::hex << std::hash<std::string>{}(joined) << "_L" << dest_level
     << (in_last_level ? "L" : "N");
  return os.str();
}