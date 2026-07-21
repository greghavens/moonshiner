#ifndef CPP_CACHEPATH_CACHE_PATH_H_
#define CPP_CACHEPATH_CACHE_PATH_H_

#include <cstddef>
#include <filesystem>
#include <string_view>

namespace cachepath {

inline constexpr std::size_t kMinKeyLength = 3;
inline constexpr std::size_t kMaxKeyLength = 128;
inline constexpr std::size_t kMaxPathLength = 4096;

enum class MapError {
  kNone,
  kInvalidRoot,
  kInvalidKey,
  kKeyTooLong,
  kPathTooLong,
};

struct MapResult {
  std::filesystem::path path;
  MapError error;

  explicit operator bool() const noexcept { return error == MapError::kNone; }
};

// Maps a compiler cache key to root/<first two bytes>/<remaining bytes>.
// This is a lexical operation: it neither creates nor resolves filesystem paths.
MapResult MapCacheKey(const std::filesystem::path& root, std::string_view key);

}  // namespace cachepath

#endif  // CPP_CACHEPATH_CACHE_PATH_H_
