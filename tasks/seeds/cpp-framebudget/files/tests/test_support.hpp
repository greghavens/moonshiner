#pragma once

#include "framebudget/decoder.hpp"

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <span>
#include <string_view>
#include <vector>

namespace test_support {

inline void append_u32(std::vector<std::uint8_t>& output,
                       std::uint32_t value) {
  for (unsigned int index = 0; index < 4U; ++index) {
    output.push_back(static_cast<std::uint8_t>(value >> (index * 8U)));
  }
}

inline void append_u64(std::vector<std::uint8_t>& output,
                       std::uint64_t value) {
  for (unsigned int index = 0; index < 8U; ++index) {
    output.push_back(static_cast<std::uint8_t>(value >> (index * 8U)));
  }
}

inline std::vector<std::uint8_t> header(std::uint32_t size,
                                        std::uint64_t work) {
  std::vector<std::uint8_t> output;
  output.reserve(framebudget::kHeaderBytes);
  append_u32(output, size);
  append_u64(output, work);
  return output;
}

inline std::vector<std::uint8_t> frame(
    std::span<const std::uint8_t> payload, std::uint64_t work) {
  auto output = header(static_cast<std::uint32_t>(payload.size()), work);
  output.insert(output.end(), payload.begin(), payload.end());
  return output;
}

inline std::span<const std::uint8_t> bytes(
    const std::vector<std::uint8_t>& value) {
  return {value.data(), value.size()};
}

inline bool payload_equals(std::span<const std::uint8_t> actual,
                           std::span<const std::uint8_t> expected) {
  return actual.size() == expected.size() &&
         std::equal(actual.begin(), actual.end(), expected.begin());
}

class Suite {
 public:
  void expect(bool condition, std::string_view message) {
    if (!condition) {
      ++failures_;
      std::cerr << "FAIL: " << message << '\n';
    }
  }

  int finish() const {
    if (failures_ == 0) {
      std::cout << "all tests passed\n";
      return 0;
    }
    std::cerr << failures_ << " assertion(s) failed\n";
    return 1;
  }

 private:
  int failures_ = 0;
};

}  // namespace test_support
