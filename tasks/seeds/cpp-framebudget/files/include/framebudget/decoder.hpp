#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <span>
#include <string>
#include <vector>

namespace framebudget {

inline constexpr std::size_t kHeaderBytes = 12;

struct Limits {
  std::uint64_t max_frame_bytes = 16U * 1024U * 1024U;
  std::uint64_t max_message_work = 64U * 1024U * 1024U;
};

enum class Status {
  NeedMore,
  FrameReady,
  MessageComplete,
  Error,
};

enum class Error {
  None,
  FrameTooLarge,
  MessageWorkExceeded,
  InvalidTerminator,
};

struct DecodeResult {
  Status status = Status::NeedMore;
  Error error = Error::None;
  std::size_t consumed = 0;
  std::span<const std::uint8_t> payload{};
  bool zero_copy = false;
  std::string message{};
};

// Budget failures use these stable diagnostics:
//   frame size <size> exceeds configured limit <limit>
//   message work exceeds configured limit <limit> (already accepted <used>,
//   frame declares <work>)

// The observer runs immediately before storage for a fragmented payload is
// reserved. It is intended for embedders that account for decoder memory.
using AllocationObserver = std::function<void(std::size_t)>;

class Decoder {
 public:
  explicit Decoder(Limits limits, AllocationObserver observer = {});

  // Processes at most one frame or terminator. The caller retains ownership of
  // input. A zero-copy payload stays valid as long as that input stays valid; a
  // buffered payload stays valid until the next call to push() or reset().
  DecodeResult push(std::span<const std::uint8_t> input);

  void reset();
  [[nodiscard]] std::uint64_t work_used() const noexcept;

 private:
  DecodeResult fail(Error error, std::size_t consumed, std::string message);

  Limits limits_;
  AllocationObserver allocation_observer_;
  std::array<std::uint8_t, kHeaderBytes> header_{};
  std::size_t header_used_ = 0;
  std::vector<std::uint8_t> owned_payload_;
  std::size_t payload_expected_ = 0;
  std::size_t payload_used_ = 0;
  bool release_owned_on_next_push_ = false;
  std::uint64_t cumulative_work_ = 0;
  bool failed_ = false;
  Error last_error_ = Error::None;
  std::string last_message_;
};

}  // namespace framebudget
