#include "pageplan/page_plan.hpp"

#include "page_math.hpp"

#include <limits>

namespace pageplan {

std::optional<std::uint64_t> required_pages(
    std::uint64_t payload_bytes) noexcept {
  constexpr auto maximum = std::numeric_limits<std::uint64_t>::max();
  if (payload_bytes > maximum - kRecordPrefixBytes) {
    return std::nullopt;
  }

  const std::uint64_t record_bytes = payload_bytes + kRecordPrefixBytes;
  return detail::ceil_div(record_bytes + 1U, kPageBytes);
}

}  // namespace pageplan
