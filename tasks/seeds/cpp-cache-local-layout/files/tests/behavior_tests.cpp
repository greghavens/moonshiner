#include "cache_chain.hpp"

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <sstream>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

namespace {

void require(bool condition, const char* message) {
  if (!condition) {
    std::cerr << "behavior test failed: " << message << '\n';
    std::exit(1);
  }
}

std::uint64_t double_bits(double value) {
  std::uint64_t bits = 0;
  std::memcpy(&bits, &value, sizeof(bits));
  return bits;
}

void append_u32(std::string& bytes, std::uint32_t value) {
  for (unsigned shift = 0; shift != 32; shift += 8) {
    bytes.push_back(static_cast<char>((value >> shift) & 0xffu));
  }
}

void append_u64(std::string& bytes, std::uint64_t value) {
  for (unsigned shift = 0; shift != 64; shift += 8) {
    bytes.push_back(static_cast<char>((value >> shift) & 0xffu));
  }
}

std::vector<cache_local::Node> fixture() {
  return {
      {10u, 3u, 0.5},
      {3u, 99u, -2.0},
      {99u, 7u, 4.0},
      {7u, 10u, 1.25},
  };
}

}  // namespace

int main() {
  using cache_local::CacheChain;
  using cache_local::Node;
  using cache_local::TraversalSample;

  static_assert(std::is_same<decltype(Node::id), std::uint32_t>::value, "id type");
  static_assert(std::is_same<decltype(Node::next_id), std::uint32_t>::value,
                "next id type");
  static_assert(std::is_same<decltype(Node::value), double>::value, "value type");
  static_assert(
      std::is_same<decltype(std::declval<const CacheChain&>().traverse(0, 0)),
                   TraversalSample>::value,
      "traversal return type");

  CacheChain chain(fixture());
  require(chain.size() == 4, "size changed");

  const std::uint32_t expected_order[] = {10u, 3u, 99u, 7u};
  std::size_t position = 0;
  for (const Node& node : chain) {
    require(position < 4, "too many iterated nodes");
    require(node.id == expected_order[position], "iteration order changed");
    ++position;
  }
  require(position == 4, "too few iterated nodes");

  const TraversalSample traversal = chain.traverse(10u, 7);
  require(traversal.steps == 7, "traversal stopped early");
  require(double_bits(traversal.sum) == double_bits(6.25),
          "traversal numerical result changed");

  const double expected_prefix_sums[] = {0.5, -1.5, 2.5, 3.75};
  for (std::size_t steps = 1; steps <= 4; ++steps) {
    const TraversalSample prefix = chain.traverse(10u, steps);
    require(prefix.steps == steps, "prefix traversal stopped early");
    require(double_bits(prefix.sum) == double_bits(expected_prefix_sums[steps - 1]),
            "traversal step order changed");
  }

  std::ostringstream output(std::ios::out | std::ios::binary);
  chain.serialize(output);

  std::string expected("CLC1", 4);
  append_u32(expected, 4u);
  for (const Node& node : fixture()) {
    append_u32(expected, node.id);
    append_u32(expected, node.next_id);
    append_u64(expected, double_bits(node.value));
  }
  require(output.str() == expected, "CLC1 serialization bytes changed");

  std::istringstream input(output.str(), std::ios::in | std::ios::binary);
  CacheChain restored = CacheChain::deserialize(input);
  const std::vector<Node> original_nodes = fixture();
  position = 0;
  for (const Node& node : restored) {
    const Node& original = original_nodes[position];
    require(node.id == original.id && node.next_id == original.next_id,
            "deserialized IDs changed");
    require(double_bits(node.value) == double_bits(original.value),
            "deserialized value changed");
    ++position;
  }
  require(position == 4, "deserialized iteration count changed");

  const std::uint32_t largest_id =
      std::numeric_limits<std::uint32_t>::max();
  CacheChain sparse({
      {largest_id, 17u, 2.25},
      {17u, largest_id, -0.75},
  });
  const TraversalSample sparse_traversal = sparse.traverse(largest_id, 5);
  require(sparse_traversal.steps == 5, "sparse-ID traversal stopped early");
  require(double_bits(sparse_traversal.sum) == double_bits(5.25),
          "sparse-ID traversal result changed");

  std::ostringstream sparse_output(std::ios::out | std::ios::binary);
  sparse.serialize(sparse_output);
  std::string expected_sparse("CLC1", 4);
  append_u32(expected_sparse, 2u);
  append_u32(expected_sparse, largest_id);
  append_u32(expected_sparse, 17u);
  append_u64(expected_sparse, double_bits(2.25));
  append_u32(expected_sparse, 17u);
  append_u32(expected_sparse, largest_id);
  append_u64(expected_sparse, double_bits(-0.75));
  require(sparse_output.str() == expected_sparse,
          "sparse-ID CLC1 serialization bytes changed");

  return 0;
}
