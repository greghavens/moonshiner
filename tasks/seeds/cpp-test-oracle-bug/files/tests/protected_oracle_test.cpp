#include "pageplan/page_plan.hpp"

#include "test_support.hpp"

#include <array>
#include <cstdint>
#include <limits>
#include <optional>

namespace {

// This oracle deliberately does not include or call the implementation's
// ceiling helper. Division and remainder avoid an overflowing pre-addition.
std::optional<std::uint64_t> reference_pages(
    std::uint64_t payload_bytes) {
  constexpr std::uint64_t maximum =
      std::numeric_limits<std::uint64_t>::max();
  if (payload_bytes > maximum - pageplan::kRecordPrefixBytes) {
    return std::nullopt;
  }

  const std::uint64_t record_bytes =
      payload_bytes + pageplan::kRecordPrefixBytes;
  const std::uint64_t complete_pages =
      record_bytes / pageplan::kPageBytes;
  const std::uint64_t partial_page =
      record_bytes % pageplan::kPageBytes == 0 ? 0 : 1;
  return complete_pages + partial_page;
}

}  // namespace

int main() {
  test_support::Suite suite;

  constexpr std::uint64_t maximum =
      std::numeric_limits<std::uint64_t>::max();
  constexpr std::uint64_t largest_valid =
      maximum - pageplan::kRecordPrefixBytes;
  constexpr std::array<std::uint64_t, 15> payloads{
      0,
      1,
      pageplan::kPageBytes - pageplan::kRecordPrefixBytes - 1U,
      pageplan::kPageBytes - pageplan::kRecordPrefixBytes,
      pageplan::kPageBytes - pageplan::kRecordPrefixBytes + 1U,
      2U * pageplan::kPageBytes - pageplan::kRecordPrefixBytes - 1U,
      2U * pageplan::kPageBytes - pageplan::kRecordPrefixBytes,
      2U * pageplan::kPageBytes - pageplan::kRecordPrefixBytes + 1U,
      largest_valid - pageplan::kPageBytes,
      largest_valid - pageplan::kPageBytes + 1U,
      largest_valid - 2U,
      largest_valid - 1U,
      largest_valid,
      largest_valid + 1U,
      maximum,
  };

  for (const std::uint64_t payload : payloads) {
    const auto expected = reference_pages(payload);
    const auto actual = pageplan::required_pages(payload);
    suite.expect(actual == expected,
                 "page count agrees with the independent arithmetic oracle");
  }

  bool ordinary_range_agrees = true;
  for (std::uint64_t payload = 0;
       payload < 3U * pageplan::kPageBytes; ++payload) {
    ordinary_range_agrees =
        ordinary_range_agrees &&
        pageplan::required_pages(payload) == reference_pages(payload);
  }
  suite.expect(ordinary_range_agrees,
               "the first three pages agree at every byte boundary");

  bool upper_range_agrees = true;
  for (std::uint64_t distance = 0;
       distance <= 2U * pageplan::kPageBytes; ++distance) {
    const std::uint64_t payload = largest_valid - distance;
    upper_range_agrees =
        upper_range_agrees &&
        pageplan::required_pages(payload) == reference_pages(payload);
  }
  suite.expect(upper_range_agrees,
               "the upper two pages agree with the independent oracle");

  bool overflow_range_rejected = true;
  for (std::uint64_t payload = largest_valid + 1U;; ++payload) {
    overflow_range_rejected =
        overflow_range_rejected &&
        !pageplan::required_pages(payload).has_value();
    if (payload == maximum) {
      break;
    }
  }
  suite.expect(overflow_range_rejected,
               "every unrepresentable prefix addition is rejected");

  const auto maximum_result = pageplan::required_pages(largest_valid);
  suite.expect(
      maximum_result.has_value() &&
          *maximum_result == (std::uint64_t{1} << 52U),
      "the largest valid record needs exactly 2^52 pages");

  return suite.finish();
}
