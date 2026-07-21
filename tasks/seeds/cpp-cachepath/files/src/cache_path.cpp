#include "cache_path.h"

#include <string>

namespace cachepath {

MapResult MapCacheKey(const std::filesystem::path& root, std::string_view key) {
  if (root.empty()) {
    return {{}, MapError::kInvalidRoot};
  }
  if (key.size() < kMinKeyLength) {
    return {{}, MapError::kInvalidKey};
  }
  if (key.size() > kMaxKeyLength) {
    return {{}, MapError::kKeyTooLong};
  }
  if (root.generic_string().size() > kMaxPathLength) {
    return {{}, MapError::kPathTooLong};
  }

  const std::filesystem::path shard{std::string(key.substr(0, 2))};
  const std::filesystem::path leaf{std::string(key.substr(2))};
  const std::filesystem::path mapped = root / shard / leaf;

  if (mapped.generic_string().size() > kMaxPathLength) {
    return {{}, MapError::kPathTooLong};
  }
  return {mapped, MapError::kNone};
}

}  // namespace cachepath
