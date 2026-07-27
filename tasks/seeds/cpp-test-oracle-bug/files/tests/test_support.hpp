#pragma once

#include <iostream>
#include <string_view>

namespace test_support {

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
