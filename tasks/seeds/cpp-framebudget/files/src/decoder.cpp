#include "framebudget/decoder.hpp"

#include <algorithm>
#include <sstream>
#include <utility>

namespace framebudget {
namespace {

std::uint32_t read_u32_le(const std::uint8_t* bytes) {
  return static_cast<std::uint32_t>(bytes[0]) |
         (static_cast<std::uint32_t>(bytes[1]) << 8U) |
         (static_cast<std::uint32_t>(bytes[2]) << 16U) |
         (static_cast<std::uint32_t>(bytes[3]) << 24U);
}

std::uint64_t read_u64_le(const std::uint8_t* bytes) {
  std::uint64_t value = 0;
  for (unsigned int index = 0; index < 8U; ++index) {
    value |= static_cast<std::uint64_t>(bytes[index]) << (index * 8U);
  }
  return value;
}

}  // namespace

Decoder::Decoder(Limits limits, AllocationObserver observer)
    : limits_(limits), allocation_observer_(std::move(observer)) {}

DecodeResult Decoder::push(std::span<const std::uint8_t> input) {
  if (failed_) {
    return {Status::Error, last_error_, 0, {}, false, last_message_};
  }

  if (release_owned_on_next_push_) {
    owned_payload_.clear();
    release_owned_on_next_push_ = false;
  }

  std::size_t cursor = 0;

  if (payload_expected_ != 0) {
    const std::size_t remaining = payload_expected_ - payload_used_;
    const std::size_t copied = std::min(remaining, input.size());
    std::copy_n(input.begin(), copied, owned_payload_.begin() + payload_used_);
    payload_used_ += copied;
    cursor += copied;

    if (payload_used_ != payload_expected_) {
      return {Status::NeedMore, Error::None, cursor, {}, false, {}};
    }

    payload_expected_ = 0;
    payload_used_ = 0;
    release_owned_on_next_push_ = true;
    return {Status::FrameReady, Error::None, cursor,
            std::span<const std::uint8_t>(owned_payload_), false, {}};
  }

  const std::size_t header_missing = kHeaderBytes - header_used_;
  const std::size_t header_copied =
      std::min(header_missing, input.size() - cursor);
  std::copy_n(input.begin() + cursor, header_copied,
              header_.begin() + header_used_);
  header_used_ += header_copied;
  cursor += header_copied;

  if (header_used_ != kHeaderBytes) {
    return {Status::NeedMore, Error::None, cursor, {}, false, {}};
  }

  const std::uint32_t declared_size = read_u32_le(header_.data());
  const std::uint64_t declared_work = read_u64_le(header_.data() + 4U);
  header_used_ = 0;

  if (declared_size == 0) {
    if (declared_work != 0) {
      return fail(Error::InvalidTerminator, cursor,
                  "message terminator must declare zero work");
    }
    cumulative_work_ = 0;
    return {Status::MessageComplete, Error::None, cursor, {}, false, {}};
  }

  cumulative_work_ += declared_work;

  const std::size_t available = input.size() - cursor;
  if (available < declared_size) {
    if (allocation_observer_) {
      allocation_observer_(declared_size);
    }
    owned_payload_.resize(declared_size);
    std::copy(input.begin() + cursor, input.end(), owned_payload_.begin());
    payload_expected_ = declared_size;
    payload_used_ = available;
  }

  if (declared_size > limits_.max_frame_bytes) {
    return fail(Error::FrameTooLarge, cursor, "frame is too large");
  }

  if (cumulative_work_ > limits_.max_message_work) {
    return fail(Error::MessageWorkExceeded, cursor,
                "message work limit exceeded");
  }

  if (available < declared_size) {
    return {Status::NeedMore, Error::None, input.size(), {}, false, {}};
  }

  const auto payload = input.subspan(cursor, declared_size);
  cursor += declared_size;
  return {Status::FrameReady, Error::None, cursor, payload, true, {}};
}

void Decoder::reset() {
  header_used_ = 0;
  owned_payload_.clear();
  payload_expected_ = 0;
  payload_used_ = 0;
  release_owned_on_next_push_ = false;
  cumulative_work_ = 0;
  failed_ = false;
  last_error_ = Error::None;
  last_message_.clear();
}

std::uint64_t Decoder::work_used() const noexcept { return cumulative_work_; }

DecodeResult Decoder::fail(Error error, std::size_t consumed,
                           std::string message) {
  failed_ = true;
  last_error_ = error;
  last_message_ = std::move(message);
  owned_payload_.clear();
  payload_expected_ = 0;
  payload_used_ = 0;
  return {Status::Error, error, consumed, {}, false, last_message_};
}

}  // namespace framebudget
