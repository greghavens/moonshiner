#pragma once

#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>

namespace file_index {

struct Entry {
    std::string key;
    std::string value;

    bool operator==(const Entry& other) const {
        return key == other.key && value == other.value;
    }
};

enum class CrashPoint {
    None,
    AfterDataShadow,
    AfterOffsetsShadow,
    AfterManifestShadow,
    AfterDataRename,
    AfterOffsetsRename,
    AfterManifestRename,
};

class SimulatedCrash : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class OldReaderError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class NoCurrentIndex : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class CorruptIndex : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// Test/support utility that creates a valid legacy file-backed index.
void create_legacy(const std::filesystem::path& directory,
                   const std::vector<Entry>& entries);

// Legacy readers retain this guard after rollout because index.v1 is kept for rollback.
std::vector<Entry> read_legacy(const std::filesystem::path& directory);

// Reads only a committed v2 generation and validates all manifest invariants.
std::vector<Entry> read_current(const std::filesystem::path& directory);

bool migration_committed(const std::filesystem::path& directory);

// Rebuilds v1 into the v2 data/offset layout. CrashPoint is deterministic fault injection.
void migrate(const std::filesystem::path& directory,
             CrashPoint crash_point = CrashPoint::None);

}  // namespace file_index
