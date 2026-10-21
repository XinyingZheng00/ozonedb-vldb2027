#ifndef OZONEDB_TEST_ENV_H
#define OZONEDB_TEST_ENV_H
// Where the tests write. OZONEDB_TEST_ROOT overrides the default of
// /tank/test/ so the suite runs without root on a machine that has no /tank.
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>

namespace ozonedb::testenv {

// The root directory, always with a trailing '/'. Created on first use.
inline std::string const& root() {
  static std::string const r = [] {
    char const* e = std::getenv("OZONEDB_TEST_ROOT");
    std::string s = (e && *e) ? e : "/tank/test/";
    if (s.back() != '/') s += '/';
    std::filesystem::create_directories(s);
    return s;
  }();
  return r;
}

// root() + sub, e.g. path("storage/").
inline std::string path(std::string const& sub) { return root() + sub; }

// A copy of ../src/config/local/test_db.json whose db_path points under
// root(). Written once per process; returns its path for DB::openDB.
inline std::string const& dbConfig() {
  static std::string const out = [] {
    std::ifstream in("../src/config/local/test_db.json");
    std::stringstream ss;
    ss << in.rdbuf();
    std::string json = ss.str();
    std::string const key = "\"db_path\": \"";
    size_t a = json.find(key);
    if (a == std::string::npos) throw std::runtime_error("test_db.json has no db_path (run the tests from build/)");
    a += key.size();
    size_t b = json.find('"', a);
    json.replace(a, b - a, path("db/"));
    std::string const file = path("test_db.json");
    std::ofstream(file) << json;
    return file;
  }();
  return out;
}

}  // namespace ozonedb::testenv
#endif  // OZONEDB_TEST_ENV_H
