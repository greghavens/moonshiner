#include "pageplan/page_plan.hpp"

#include "page_math.hpp"
#include "test_support.hpp"

#include <cstdint>
#include <limits>

int main() {
  test_support::Suite suite;

  {
    const auto result = pageplan::required_pages(0);
    suite.expect(result.has_value() && *result == 1,
                 "an empty payload still occupies a prefix page");
  }

  {
    constexpr std::uint64_t payload =
        pageplan::kPageBytes - pageplan::kRecordPrefixBytes;
    const auto expected = pageplan::detail::ceil_div(
        payload + pageplan::kRecordPrefixBytes, pageplan::kPageBytes);
    const auto result = pageplan::required_pages(payload);
    suite.expect(result.has_value() && *result == expected,
                 "a record ending exactly on a page uses that page only");
  }

  {
    constexpr std::uint64_t payload =
        pageplan::kPageBytes - pageplan::kRecordPrefixBytes + 1U;
    const auto expected = pageplan::detail::ceil_div(
        payload + pageplan::kRecordPrefixBytes, pageplan::kPageBytes);
    const auto result = pageplan::required_pages(payload);
    suite.expect(result.has_value() && *result == expected,
                 "the first byte beyond a page needs another page");
  }

  {
    constexpr std::uint64_t maximum =
        std::numeric_limits<std::uint64_t>::max();
    constexpr std::uint64_t payload =
        maximum - pageplan::kRecordPrefixBytes;
    const auto expected = pageplan::detail::ceil_div(
        payload + pageplan::kRecordPrefixBytes, pageplan::kPageBytes);
    const auto result = pageplan::required_pages(payload);
    suite.expect(result.has_value() && *result == expected,
                 "the largest representable record has a page count");
  }

  {
    constexpr std::uint64_t maximum =
        std::numeric_limits<std::uint64_t>::max();
    constexpr std::uint64_t payload =
        maximum - pageplan::kRecordPrefixBytes + 1U;
    suite.expect(!pageplan::required_pages(payload).has_value(),
                 "a payload whose prefix addition overflows is rejected");
  }

  return suite.finish();
}
