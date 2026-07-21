#include "test_support.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <span>
#include <string>

using framebudget::Decoder;
using framebudget::Error;
using framebudget::Limits;
using framebudget::Status;
using test_support::Suite;

int main() {
  Suite suite;

  {
    std::size_t allocations = 0;
    Decoder decoder(Limits{4, 100},
                    [&](std::size_t) { ++allocations; });
    const auto oversized = test_support::header(5, 1);
    const auto result = decoder.push(test_support::bytes(oversized));

    suite.expect(result.status == Status::Error,
                 "an oversized declaration is rejected from its header");
    suite.expect(result.error == Error::FrameTooLarge,
                 "an oversized declaration has the frame-size error code");
    suite.expect(allocations == 0,
                 "an oversized declaration is rejected before allocation");
    suite.expect(result.message ==
                     "frame size 5 exceeds configured limit 4",
                 "the frame-size diagnostic contains declaration and limit");
  }

  {
    std::size_t allocations = 0;
    Decoder decoder(Limits{8, 5},
                    [&](std::size_t) { ++allocations; });
    const std::array<std::uint8_t, 1> first_payload{0x41};
    const auto first_wire = test_support::frame(first_payload, 4);
    suite.expect(decoder.push(test_support::bytes(first_wire)).status ==
                     Status::FrameReady,
                 "the first in-budget frame is accepted");

    const auto over_work = test_support::header(4, 2);
    const auto result = decoder.push(test_support::bytes(over_work));
    suite.expect(result.status == Status::Error,
                 "cumulative work is rejected from the next header");
    suite.expect(result.error == Error::MessageWorkExceeded,
                 "cumulative work has the work-budget error code");
    suite.expect(allocations == 0,
                 "over-budget work is rejected before payload allocation");
    suite.expect(
        result.message ==
            "message work exceeds configured limit 5 (already accepted 4, frame declares 2)",
        "the work diagnostic preserves all relevant budget values");
  }

  {
    constexpr std::uint64_t maximum =
        std::numeric_limits<std::uint64_t>::max();
    Decoder decoder(Limits{1, maximum});
    const std::array<std::uint8_t, 1> payload{0x7f};
    const auto first = test_support::frame(payload, maximum - 1);
    suite.expect(decoder.push(test_support::bytes(first)).status ==
                     Status::FrameReady,
                 "work immediately below UINT64_MAX is accepted");

    const auto wrapping = test_support::frame(payload, 2);
    const auto result = decoder.push(test_support::bytes(wrapping));
    suite.expect(result.status == Status::Error,
                 "work addition that would wrap is rejected");
    suite.expect(result.error == Error::MessageWorkExceeded,
                 "wrapped work reports the work-budget error");
    suite.expect(
        result.message ==
            "message work exceeds configured limit 18446744073709551615 (already accepted 18446744073709551614, frame declares 2)",
        "the wrap-safe diagnostic reports unmodified operands");
  }

  {
    std::size_t allocations = 0;
    Decoder decoder(Limits{2, 10},
                    [&](std::size_t) { ++allocations; });
    const auto oversized = test_support::header(3, 1);
    framebudget::DecodeResult result;
    for (const std::uint8_t byte : oversized) {
      result = decoder.push(std::span<const std::uint8_t>(&byte, 1));
    }
    suite.expect(result.status == Status::Error,
                 "an oversized fragmented header is rejected when complete");
    suite.expect(allocations == 0,
                 "fragmented headers are still checked before allocation");
    suite.expect(result.message ==
                     "frame size 3 exceeds configured limit 2",
                 "fragmented and contiguous declarations report identically");
  }

  {
    std::size_t allocations = 0;
    Decoder decoder(Limits{3, 7},
                    [&](std::size_t) { ++allocations; });
    const std::array<std::uint8_t, 3> payload{8, 9, 10};
    const auto wire = test_support::frame(payload, 7);
    const auto result = decoder.push(test_support::bytes(wire));
    suite.expect(result.status == Status::FrameReady,
                 "declarations exactly at both limits are accepted");
    suite.expect(result.zero_copy,
                 "an in-budget contiguous frame remains zero-copy");
    suite.expect(result.payload.data() ==
                     wire.data() + framebudget::kHeaderBytes,
                 "the accepted payload aliases caller input");
    suite.expect(allocations == 0,
                 "zero-copy delivery does not notify an allocation");

    const auto terminator = test_support::header(0, 0);
    suite.expect(decoder.push(test_support::bytes(terminator)).status ==
                     Status::MessageComplete,
                 "an exact-budget message can be terminated");

    const auto next = test_support::frame(payload, 7);
    suite.expect(decoder.push(test_support::bytes(next)).status ==
                     Status::FrameReady,
                 "the next message receives a fresh work budget");
  }

  return suite.finish();
}
