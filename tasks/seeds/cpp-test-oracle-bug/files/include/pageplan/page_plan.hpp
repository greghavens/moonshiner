#pragma once

#include <cstdint>
#include <optional>

namespace pageplan {

inline constexpr std::uint64_t kPageBytes = 4096;
inline constexpr std::uint64_t kRecordPrefixBytes = 24;

// Returns the number of pages occupied by the record prefix and payload:
//
//   ceil((kRecordPrefixBytes + payload_bytes) / kPageBytes)
//
// Returns nullopt if adding the prefix to payload_bytes is not representable
// by uint64_t. Every other input must produce the mathematical ceiling.
[[nodiscard]] std::optional<std::uint64_t> required_pages(
    std::uint64_t payload_bytes) noexcept;

}  // namespace pageplan
