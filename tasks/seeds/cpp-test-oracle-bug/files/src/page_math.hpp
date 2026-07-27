#pragma once

#include <cstdint>

namespace pageplan::detail {

[[nodiscard]] constexpr std::uint64_t ceil_div(std::uint64_t value,
                                               std::uint64_t divisor) noexcept {
  return (value + divisor - 1U) / divisor;
}

}  // namespace pageplan::detail
