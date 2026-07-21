#include "test_support.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>

using framebudget::Decoder;
using framebudget::Limits;
using framebudget::Status;
using test_support::Suite;

int main() {
  Suite suite;

  {
    Decoder decoder(Limits{64, 100});
    const std::array<std::uint8_t, 3> payload{0x10, 0x20, 0x30};
    const auto wire = test_support::frame(payload, 7);
    const auto result = decoder.push(test_support::bytes(wire));

    suite.expect(result.status == Status::FrameReady,
                 "a complete frame is delivered");
    suite.expect(result.zero_copy, "a complete frame is zero-copy");
    suite.expect(result.payload.data() ==
                     wire.data() + framebudget::kHeaderBytes,
                 "the zero-copy view points into caller input");
    suite.expect(test_support::payload_equals(result.payload, payload),
                 "the frame payload is intact");
    suite.expect(result.consumed == wire.size(),
                 "the complete frame is fully consumed");
    suite.expect(decoder.work_used() == 7, "frame work is recorded");

    const auto terminator = test_support::header(0, 0);
    const auto done = decoder.push(test_support::bytes(terminator));
    suite.expect(done.status == Status::MessageComplete,
                 "the terminator completes the message");
    suite.expect(decoder.work_used() == 0,
                 "the terminator resets message work");
  }

  {
    std::size_t allocations = 0;
    Decoder decoder(Limits{64, 100},
                    [&](std::size_t) { ++allocations; });
    const std::array<std::uint8_t, 4> payload{1, 2, 3, 4};
    const auto wire = test_support::frame(payload, 9);
    const auto all = test_support::bytes(wire);

    const auto first = decoder.push(all.first(5));
    suite.expect(first.status == Status::NeedMore,
                 "a partial header needs more input");
    suite.expect(allocations == 0,
                 "a partial header does not allocate payload storage");

    const auto second = decoder.push(all.subspan(5, 9));
    suite.expect(second.status == Status::NeedMore,
                 "a fragmented payload needs more input");
    suite.expect(allocations == 1,
                 "a valid fragmented payload allocates once");
    suite.expect(decoder.work_used() == 9,
                 "fragmented frame work is charged once");

    const auto third = decoder.push(all.subspan(14));
    suite.expect(third.status == Status::FrameReady,
                 "the completed fragmented frame is delivered");
    suite.expect(!third.zero_copy,
                 "a fragmented payload is served from decoder storage");
    suite.expect(test_support::payload_equals(third.payload, payload),
                 "the fragmented payload is reassembled");
    suite.expect(decoder.work_used() == 9,
                 "additional payload chunks do not charge work again");
  }

  return suite.finish();
}
