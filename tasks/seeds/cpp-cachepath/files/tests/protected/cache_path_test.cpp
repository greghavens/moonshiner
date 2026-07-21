#include "cache_path.h"

#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <string>
#include <string_view>

namespace {

int failures = 0;

void Fail(std::string_view label, std::string_view detail) {
  ++failures;
  std::cerr << "FAIL: " << label << ": " << detail << '\n';
}

void ExpectPath(std::string_view label, const std::filesystem::path& root,
                std::string_view key, const std::filesystem::path& expected) {
  const cachepath::MapResult result = cachepath::MapCacheKey(root, key);
  if (!result) {
    Fail(label, "valid key was rejected");
  } else if (result.path != expected) {
    Fail(label, "stable layout changed");
  }
}

void ExpectError(std::string_view label, const std::filesystem::path& root,
                 std::string_view key, cachepath::MapError expected) {
  const cachepath::MapResult result = cachepath::MapCacheKey(root, key);
  if (result.error != expected) {
    Fail(label, "wrong error");
  }
  if (!result.path.empty()) {
    Fail(label, "error result exposed a path");
  }
}

void TestStableLayout() {
  const std::filesystem::path root{"cache-root"};
  ExpectPath("ordinary key", root, "abcdef012345", root / "ab" / "cdef012345");
  ExpectPath("minimum key", root, "a_0", root / "a_" / "0");
  ExpectPath("allowed punctuation", root, "A-b_c.d9", root / "A-" / "b_c.d9");
  ExpectPath("dot inside shard", root, "a.bc", root / "a." / "bc");
  ExpectPath("dot inside leaf", root, "ab.c", root / "ab" / ".c");
}

void TestSeparatorsAndDotComponents() {
  const std::filesystem::path root{"cache"};
  ExpectError("POSIX separator", root, "ab/../../outside",
              cachepath::MapError::kInvalidKey);
  ExpectError("Windows separator", root, R"(ab\..\..\outside)",
              cachepath::MapError::kInvalidKey);
  ExpectError("dot shard", root, "..payload", cachepath::MapError::kInvalidKey);
  ExpectError("dot leaf", root, "ab..", cachepath::MapError::kInvalidKey);
  ExpectError("single-dot leaf", root, "ab.", cachepath::MapError::kInvalidKey);
}

void TestPlatformPrefixesAndAlphabet() {
  const std::filesystem::path root{"cache"};
  ExpectError("drive prefix", root, "C:payload", cachepath::MapError::kInvalidKey);
  ExpectError("drive path", root, R"(C:\payload)", cachepath::MapError::kInvalidKey);
  ExpectError("POSIX absolute", root, "/etc/passwd", cachepath::MapError::kInvalidKey);
  ExpectError("UNC prefix", root, R"(\\server\share)", cachepath::MapError::kInvalidKey);
  ExpectError("embedded colon", root, "ab:name", cachepath::MapError::kInvalidKey);
  ExpectError("question mark", root, "ab?name", cachepath::MapError::kInvalidKey);
  const std::string control{"ab\x01name", 7};
  ExpectError("control byte", root, control, cachepath::MapError::kInvalidKey);
}

void TestCompleteByteAlphabet() {
  const std::filesystem::path root{"cache"};
  for (unsigned int value = 0; value < 256; ++value) {
    std::string key{"axb"};
    key[1] = static_cast<char>(value);
    const bool allowed =
        (value >= static_cast<unsigned int>('a') &&
         value <= static_cast<unsigned int>('z')) ||
        (value >= static_cast<unsigned int>('A') &&
         value <= static_cast<unsigned int>('Z')) ||
        (value >= static_cast<unsigned int>('0') &&
         value <= static_cast<unsigned int>('9')) ||
        value == static_cast<unsigned int>('-') ||
        value == static_cast<unsigned int>('_') ||
        value == static_cast<unsigned int>('.');

    const cachepath::MapResult result = cachepath::MapCacheKey(root, key);
    if (allowed) {
      const std::filesystem::path expected =
          root / std::string(key.substr(0, 2)) / "b";
      if (!result || result.path != expected) {
        Fail("complete byte alphabet", "an allowed ASCII byte was rejected");
        return;
      }
    } else if (result.error != cachepath::MapError::kInvalidKey ||
               !result.path.empty()) {
      Fail("complete byte alphabet", "a byte outside the alphabet was accepted");
      return;
    }
  }
}

void TestIntegerAndPathBounds() {
  const std::filesystem::path root{"cache"};
  ExpectError("empty root", {}, "abcdef", cachepath::MapError::kInvalidRoot);
  ExpectError("empty key", root, "", cachepath::MapError::kInvalidKey);
  ExpectError("short key", root, "ab", cachepath::MapError::kInvalidKey);

  const std::string longest(cachepath::kMaxKeyLength, 'a');
  ExpectPath("maximum key length", root, longest,
             root / "aa" / std::string(cachepath::kMaxKeyLength - 2, 'a'));
  const std::string oversized(cachepath::kMaxKeyLength + 1, 'a');
  ExpectError("oversized key", root, oversized, cachepath::MapError::kKeyTooLong);

  // For key "abcd", joining adds "/ab/cd", exactly six bytes.
  const std::filesystem::path exact_root{
      std::string(cachepath::kMaxPathLength - 6, 'r')};
  ExpectPath("exact path bound", exact_root, "abcd", exact_root / "ab" / "cd");
  const std::filesystem::path long_root{
      std::string(cachepath::kMaxPathLength - 5, 'r')};
  ExpectError("path over bound", long_root, "abcd", cachepath::MapError::kPathTooLong);
}

}  // namespace

int main() {
  TestStableLayout();
  TestSeparatorsAndDotComponents();
  TestPlatformPrefixesAndAlphabet();
  TestCompleteByteAlphabet();
  TestIntegerAndPathBounds();

  if (failures != 0) {
    std::cerr << failures << " assertion(s) failed\n";
    return EXIT_FAILURE;
  }
  std::cout << "all cache-path tests passed\n";
  return EXIT_SUCCESS;
}
