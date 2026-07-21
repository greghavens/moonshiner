#ifndef FRAMING_LENGTH_PREFIXED_DECODER_H_
#define FRAMING_LENGTH_PREFIXED_DECODER_H_

#include <cstddef>
#include <cstdint>
#include <memory_resource>
#include <span>
#include <vector>

namespace framing {

enum class DecodeStatus {
  kComplete,
  kNeedMore,
  kFrameTooLarge,
};

struct DecodeResult {
  DecodeStatus status;
  std::span<const std::byte> payload;
  std::size_t bytes_consumed;
  std::size_t bytes_needed;
};

// Reads frames consisting of an unsigned 64-bit big-endian payload length
// followed by that many payload bytes. Complete frames are returned as views
// into the caller's input. For an incomplete, otherwise valid frame, staging_
// reserves enough space for a later buffered read.
class LengthPrefixedDecoder {
 public:
  static constexpr std::size_t kPrefixSize = sizeof(std::uint64_t);

  explicit LengthPrefixedDecoder(
      std::uint64_t max_payload_size,
      std::pmr::memory_resource* memory = std::pmr::get_default_resource())
      : max_payload_size_(max_payload_size), staging_(memory) {}

  [[nodiscard]] std::uint64_t max_payload_size() const noexcept {
    return max_payload_size_;
  }

  [[nodiscard]] DecodeResult read(
      std::span<const std::byte> input) {
    if (input.size() < kPrefixSize) {
      return {DecodeStatus::kNeedMore, {}, 0, kPrefixSize};
    }

    const std::uint64_t payload_size = read_big_endian_u64(input.data());
    const std::size_t frame_size =
        kPrefixSize + static_cast<std::size_t>(payload_size);

    if (input.size() < frame_size ||
        static_cast<std::size_t>(payload_size) >
            input.size() - kPrefixSize) {
      staging_.reserve(frame_size);
      return {DecodeStatus::kNeedMore, {}, 0, frame_size};
    }

    return {DecodeStatus::kComplete,
            input.subspan(kPrefixSize,
                          static_cast<std::size_t>(payload_size)),
            frame_size,
            frame_size};
  }

 private:
  static std::uint64_t read_big_endian_u64(const std::byte* bytes) noexcept {
    std::uint64_t value = 0;
    for (std::size_t i = 0; i < kPrefixSize; ++i) {
      value = (value << 8U) |
              static_cast<std::uint64_t>(std::to_integer<unsigned int>(bytes[i]));
    }
    return value;
  }

  std::uint64_t max_payload_size_;
  std::pmr::vector<std::byte> staging_;
};

}  // namespace framing

#endif  // FRAMING_LENGTH_PREFIXED_DECODER_H_
