#include <string>
#include <vector>
#ifndef OZONEDB_HELPER_H
#define OZONEDB_HELPER_H

// Prefix of a file name of the form prefix/suffix; "" if no match.
std::string getPrefix(std::string const& filename);

// Suffix of a file name of the form prefix/suffix; "" if no match.
std::string getSuffix(std::string const& filename);

// Trailing number of a string; -1 if none.
int getNumberInTheEnd(std::string const& filename);

int getSSTLayerNumber(std::string const& filename);
// Per-process fingerprint: a random UUID followed by a timestamp.
std::string generateFingerprint();

int getNumberBeforeUnderscore(std::string const& filename);

// Stable compaction task identifier: the same input files, destination
// level and in-last-level flag give the same string on every peer.
// Used by the [COMPACTION_NEEDED] and [COMPACTION_COMMITTED] log lines.
std::string formatTaskId(std::vector<std::string> const& input_files,
                         int dest_level, bool in_last_level);
#endif