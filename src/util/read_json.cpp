#include "read_json.h"
#include <algorithm>
#include <fstream>
#include <iostream>
#include <sstream>

// Trim leading and trailing spaces.
std::string trim(std::string const& str) {
  size_t first = str.find_first_not_of(' ');
  size_t last = str.find_last_not_of(' ');
  if (first == std::string::npos || last == std::string::npos)
    return "";
  return str.substr(first, last - first + 1);
}

// Parse a flat JSON object, one "key": value pair per line, into strings.
// Every value is a string; an array stays a literal like "[1, 2]".
std::map<std::string, std::string> parseJSON(std::string const& path) {
  std::map<std::string, std::string> result;
  std::ifstream ifs(path);
  if (!ifs.is_open()) {
    std::cerr << "Failed to open JSON file." << std::endl;
    return result;
  }

  // Read the whole file
  std::string json_str((std::istreambuf_iterator<char>(ifs)), std::istreambuf_iterator<char>());

  std::istringstream iss(json_str);

  std::string line;
  while (std::getline(iss, line)) {
    line = trim(line);
    if (line.empty() || line.front() == '{' || line.front() == '}')
      continue;
    size_t comma_pos = line.find_last_of(',');
    if (comma_pos != std::string::npos) {
      line = line.substr(0, comma_pos);
    }
    size_t colon_pos = line.find(':');
    if (colon_pos != std::string::npos) {
      std::string key = trim(line.substr(0, colon_pos));
      std::string value = trim(line.substr(colon_pos + 1));
      // Strip the quotes
      key.erase(std::remove_if(key.begin(), key.end(), [](char c) { return c == '"'; }), key.end());
      value.erase(std::remove_if(value.begin(), value.end(), [](char c) { return c == '"'; }), value.end());
      result[key] = value;
    }
  }

  return result;
}

std::vector<std::string> jsonArrayToVector(std::string & json) {
  std::vector<std::string> vec;
  std::string delimiter = ",";
  size_t pos = 0;
  std::string token;

  // Strip the surrounding brackets
  json = trim(json);
  if (json[0] == '[') {
    json.erase(0, 1);
  }
  if (json[json.length() - 1] == ']') {
    json.erase(json.length() - 1, 1);
  }

  json = trim(json);

  // No comma: zero or one element
  if (json.find(delimiter) == std::string::npos) {
    if (!json.empty()) {
      vec.push_back(json);
    }
    return vec;
  }

  // Split on commas
  while ((pos = json.find(delimiter)) != std::string::npos) {
    token = json.substr(0, pos);
    token = trim(token);
    vec.push_back(token);
    json.erase(0, pos + delimiter.length());
  }

  // The last element has no trailing comma
  if (!json.empty()) {
    json = trim(json);
    vec.push_back(json);
  }

  return vec;
}