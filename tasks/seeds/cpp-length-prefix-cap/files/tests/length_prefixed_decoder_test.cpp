#include "framing/length_prefixed_decoder.h"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <exception>
#include <iostream>
#include <limits>
#include <memory_resource>
#include <new>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

using framing::DecodeStatus;
using framing::LengthPrefixedDecoder;

class RecordingResource final : public std::pmr::memory_resource {
 public:
  explicit RecordingResource(std::size_t largest_allowed)
      : largest_allowed_(largest_allowed) {}

  [[nodiscard]] std::size_t allocations() const noexcept {
    return allocations_;
  }

 private:
  void* do_allocate(std::size_t bytes, std::size_t alignment) override {
    ++allocations_;
    if (bytes > largest_allowed_) {
      throw std::bad_alloc();
    }
    return std::pmr::new_delete_resource()->allocate(bytes, alignment);
  }

  void do_deallocate(void* pointer, std::size_t bytes,
                     std::size_t alignment) override {
    std::pmr::new_delete_resource()->deallocate(pointer, bytes, alignment);
  }

  bool do_is_equal(const std::pmr::memory_resource& other) const
      noexcept override {
    return this == &other;
  }

  std::size_t largest_allowed_;
  std::size_t allocations_ = 0;
};

void require(bool condition, std::string_view message) {
  if (!condition) {
    throw std::runtime_error(std::string(message));
  }
}

void write_prefix(std::span<std::byte> destination, std::uint64_t value) {
  require(destination.size() >= LengthPrefixedDecoder::kPrefixSize,
          "prefix destination is too short");
  for (std::size_t i = 0; i < LengthPrefixedDecoder::kPrefixSize; ++i) {
    const std::size_t shift =
        (LengthPrefixedDecoder::kPrefixSize - 1U - i) * 8U;
    destination[i] = static_cast<std::byte>((value >> shift) & 0xffU);
  }
}

std::vector<std::byte> frame(std::uint64_t declared_size,
                             std::initializer_list<unsigned int> payload) {
  std::vector<std::byte> bytes(LengthPrefixedDecoder::kPrefixSize +
                               payload.size());
  write_prefix(bytes, declared_size);
  std::transform(payload.begin(), payload.end(),
                 bytes.begin() +
                     static_cast<std::ptrdiff_t>(
                         LengthPrefixedDecoder::kPrefixSize),
                 [](unsigned int value) {
                   return static_cast<std::byte>(value);
                 });
  return bytes;
}

void oversized_prefix_is_rejected_before_reservation() {
  RecordingResource memory(64);
  LengthPrefixedDecoder decoder(32, &memory);
  auto bytes = frame(4096, {});

  const auto result = decoder.read(bytes);

  require(result.status == DecodeStatus::kFrameTooLarge,
          "an over-limit prefix must be rejected");
  require(result.payload.empty(), "a rejected frame must have no payload");
  require(result.bytes_consumed == 0,
          "a rejected frame must not consume input");
  require(result.bytes_needed == 0,
          "a rejected frame must not request more input");
  require(memory.allocations() == 0,
          "an over-limit prefix must be rejected before allocation");
}

void overflowing_total_size_is_rejected() {
  RecordingResource memory(64);
  LengthPrefixedDecoder decoder(std::numeric_limits<std::uint64_t>::max(),
                                &memory);
  auto bytes = frame(std::numeric_limits<std::uint64_t>::max(), {});

  const auto result = decoder.read(bytes);

  require(result.status == DecodeStatus::kFrameTooLarge,
          "prefix plus payload overflow must be rejected");
  require(result.bytes_consumed == 0,
          "overflow rejection must not consume input");
  require(memory.allocations() == 0,
          "overflow must be detected before allocation");
}

void smallest_overflowing_total_size_is_rejected() {
  RecordingResource memory(64);
  LengthPrefixedDecoder decoder(std::numeric_limits<std::uint64_t>::max(),
                                &memory);
  const auto largest_frame_payload =
      static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max() -
                                 LengthPrefixedDecoder::kPrefixSize);
  auto bytes = frame(largest_frame_payload + 1U, {});

  const auto result = decoder.read(bytes);

  require(result.status == DecodeStatus::kFrameTooLarge,
          "the first payload whose total size overflows must be rejected");
  require(result.bytes_consumed == 0,
          "boundary overflow rejection must not consume input");
  require(result.bytes_needed == 0,
          "boundary overflow rejection must not request more input");
  require(memory.allocations() == 0,
          "boundary overflow must be detected before allocation");
}

void incomplete_prefix_keeps_waiting_without_allocation() {
  RecordingResource memory(64);
  LengthPrefixedDecoder decoder(32, &memory);
  const std::array<std::byte, 3> bytes{
      std::byte{0}, std::byte{0}, std::byte{0}};

  const auto result = decoder.read(bytes);

  require(result.status == DecodeStatus::kNeedMore,
          "a partial prefix must remain incomplete");
  require(result.bytes_consumed == 0,
          "a partial prefix must not consume input");
  require(result.bytes_needed == LengthPrefixedDecoder::kPrefixSize,
          "a partial prefix must report the prefix size");
  require(memory.allocations() == 0,
          "a partial prefix must not allocate");
}

void valid_partial_payload_preserves_staging_behavior() {
  RecordingResource memory(64);
  LengthPrefixedDecoder decoder(5, &memory);
  auto bytes = frame(5, {0x11U, 0x22U, 0x33U});

  const auto result = decoder.read(bytes);

  require(result.status == DecodeStatus::kNeedMore,
          "a valid partial payload must remain incomplete");
  require(result.bytes_consumed == 0,
          "a partial payload must not consume input");
  require(result.bytes_needed == LengthPrefixedDecoder::kPrefixSize + 5U,
          "a partial payload must report the complete frame size");
  require(memory.allocations() == 1,
          "a valid partial payload must retain its staging reservation");
}

void exact_limit_frame_is_a_zero_copy_read() {
  RecordingResource memory(64);
  LengthPrefixedDecoder decoder(5, &memory);
  auto bytes = frame(5, {0x10U, 0x20U, 0x30U, 0x40U, 0x50U});

  const auto result = decoder.read(bytes);

  require(result.status == DecodeStatus::kComplete,
          "an exact-limit frame must be accepted");
  require(result.bytes_consumed == bytes.size(),
          "a complete frame must report its consumed size");
  require(result.bytes_needed == bytes.size(),
          "a complete frame must report its frame size");
  require(result.payload.size() == 5,
          "the complete payload must have its declared size");
  require(result.payload.data() ==
              bytes.data() + LengthPrefixedDecoder::kPrefixSize,
          "a complete payload must view the caller's input");
  require(result.payload[2] == std::byte{0x30},
          "the payload view must contain the input bytes");
  require(memory.allocations() == 0,
          "a complete frame must not allocate");
}

void larger_valid_frame_uses_configured_limit() {
  RecordingResource memory(64);
  LengthPrefixedDecoder decoder(16, &memory);
  auto bytes = frame(9, {0x10U, 0x20U, 0x30U, 0x40U, 0x50U,
                         0x60U, 0x70U, 0x80U, 0x90U});

  const auto result = decoder.read(bytes);

  require(result.status == DecodeStatus::kComplete,
          "a valid frame must not be rejected by a fixed small cap");
  require(result.payload.size() == 9,
          "a larger valid payload must keep its declared size");
  require(result.bytes_consumed == bytes.size(),
          "a larger valid frame must report its consumed size");
  require(result.bytes_needed == bytes.size(),
          "a larger valid frame must report its frame size");
  require(result.payload.data() ==
              bytes.data() + LengthPrefixedDecoder::kPrefixSize,
          "a larger valid payload must view the caller's input");
  require(memory.allocations() == 0,
          "a larger complete frame must not allocate");
}

void trailing_input_is_not_consumed() {
  RecordingResource memory(64);
  LengthPrefixedDecoder decoder(16, &memory);
  auto bytes = frame(2, {0xa1U, 0xb2U, 0xc3U, 0xd4U});

  const auto result = decoder.read(bytes);

  require(result.status == DecodeStatus::kComplete,
          "the first complete frame must be returned");
  require(result.payload.size() == 2,
          "trailing bytes must not become part of the payload");
  require(result.bytes_consumed == LengthPrefixedDecoder::kPrefixSize + 2U,
          "trailing bytes must remain unconsumed");
  require(result.payload.data() ==
              bytes.data() + LengthPrefixedDecoder::kPrefixSize,
          "a frame with trailing input must still be zero-copy");
  require(memory.allocations() == 0,
          "a complete frame with trailing input must not allocate");
}

void complete_over_limit_frame_is_rejected() {
  RecordingResource memory(64);
  LengthPrefixedDecoder decoder(3, &memory);
  auto bytes = frame(4, {1U, 2U, 3U, 4U});

  const auto result = decoder.read(bytes);

  require(result.status == DecodeStatus::kFrameTooLarge,
          "a complete over-limit frame must still be rejected");
  require(result.bytes_consumed == 0,
          "an over-limit complete frame must not be consumed");
  require(memory.allocations() == 0,
          "an over-limit complete frame must not allocate");
}

template <typename Test>
void run(std::string_view name, Test&& test, int& failures) {
  try {
    std::forward<Test>(test)();
    std::cout << "PASS " << name << '\n';
  } catch (const std::exception& error) {
    ++failures;
    std::cerr << "FAIL " << name << ": " << error.what() << '\n';
  } catch (...) {
    ++failures;
    std::cerr << "FAIL " << name << ": unknown exception\n";
  }
}

}  // namespace

int main() {
  int failures = 0;
  run("oversized prefix is rejected before reservation",
      oversized_prefix_is_rejected_before_reservation, failures);
  run("overflowing total size is rejected",
      overflowing_total_size_is_rejected, failures);
  run("smallest overflowing total size is rejected",
      smallest_overflowing_total_size_is_rejected, failures);
  run("incomplete prefix keeps waiting without allocation",
      incomplete_prefix_keeps_waiting_without_allocation, failures);
  run("valid partial payload preserves staging behavior",
      valid_partial_payload_preserves_staging_behavior, failures);
  run("exact limit frame is a zero-copy read",
      exact_limit_frame_is_a_zero_copy_read, failures);
  run("larger valid frame uses configured limit",
      larger_valid_frame_uses_configured_limit, failures);
  run("trailing input is not consumed", trailing_input_is_not_consumed,
      failures);
  run("complete over-limit frame is rejected",
      complete_over_limit_frame_is_rejected, failures);

  if (failures != 0) {
    std::cerr << failures << " test(s) failed\n";
    return 1;
  }
  return 0;
}
