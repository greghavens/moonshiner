#include "cache_chain.hpp"

#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <new>
#include <utility>
#include <vector>

namespace {
std::size_t allocation_count = 0;
}

void* operator new(std::size_t size) {
  ++allocation_count;
  if (void* allocation = std::malloc(size)) {
    return allocation;
  }
  throw std::bad_alloc();
}

void operator delete(void* allocation) noexcept { std::free(allocation); }

void operator delete(void* allocation, std::size_t) noexcept {
  std::free(allocation);
}

namespace {

std::uint64_t double_bits(double value) {
  std::uint64_t bits = 0;
  std::memcpy(&bits, &value, sizeof(bits));
  return bits;
}

double value_for(std::uint32_t id) {
  const int centered = static_cast<int>(id % 29u) - 14;
  return 1.0 + static_cast<double>(centered) * 0.03125;
}

int fail(const char* message) {
  std::cerr << "layout benchmark failed: " << message << '\n';
  return 1;
}

}  // namespace

int main() {
  constexpr std::uint32_t kNodeCount = 8192;
  constexpr std::uint32_t kStride = 4093;
  constexpr std::size_t kRounds = 32;
  constexpr std::size_t kSteps =
      static_cast<std::size_t>(kNodeCount) * kRounds;

  // The multiplier is odd, so this is a deterministic permutation of all IDs.
  // Storage order deliberately differs from traversal order.
  std::vector<cache_local::Node> nodes;
  nodes.reserve(kNodeCount);
  for (std::uint32_t slot = 0; slot < kNodeCount; ++slot) {
    const std::uint32_t id = (slot * 4051u) % kNodeCount;
    nodes.push_back({id, (id + kStride) % kNodeCount, value_for(id)});
  }

  const std::size_t allocations_before = allocation_count;
  cache_local::CacheChain chain(std::move(nodes));
  const std::size_t index_allocations = allocation_count - allocations_before;
  if (index_allocations > 64) {
    return fail("dense index construction still allocates one node per ID");
  }

  const cache_local::TraversalSample sample = chain.traverse(0u, kSteps);

  double expected_sum = 0.0;
  std::uint32_t id = 0;
  for (std::size_t step = 0; step < kSteps; ++step) {
    expected_sum += value_for(id);
    id = (id + kStride) % kNodeCount;
  }

  if (sample.steps != kSteps) {
    return fail("traversal did not complete");
  }
  if (double_bits(sample.sum) != double_bits(expected_sum)) {
    return fail("cache change altered the bit-for-bit sum");
  }
  if (sample.index_reads != kSteps) {
    return fail("unexpected number of index reads");
  }
  if (sample.index_pointer_chases != 0) {
    return fail("hot traversal still pointer-chases a node-based index");
  }

  return 0;
}
