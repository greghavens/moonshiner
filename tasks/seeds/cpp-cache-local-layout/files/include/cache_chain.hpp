#pragma once

#include <cstddef>
#include <cstdint>
#include <iosfwd>
#include <memory>
#include <vector>

namespace cache_local {

struct Node {
  std::uint32_t id;
  std::uint32_t next_id;
  double value;
};

// The counters make layout costs observable without relying on noisy timings.
struct TraversalSample {
  double sum = 0.0;
  std::size_t steps = 0;
  std::size_t index_reads = 0;
  std::size_t index_pointer_chases = 0;
};

class CacheChain {
 public:
  using const_iterator = std::vector<Node>::const_iterator;

  explicit CacheChain(std::vector<Node> nodes);
  ~CacheChain();

  CacheChain(CacheChain&&) noexcept;
  CacheChain& operator=(CacheChain&&) noexcept;
  CacheChain(const CacheChain&) = delete;
  CacheChain& operator=(const CacheChain&) = delete;

  std::size_t size() const noexcept;
  const_iterator begin() const noexcept;
  const_iterator end() const noexcept;

  TraversalSample traverse(std::uint32_t start_id,
                           std::size_t step_limit) const;

  void serialize(std::ostream& output) const;
  static CacheChain deserialize(std::istream& input);

 private:
  struct State;
  std::unique_ptr<State> state_;
};

}  // namespace cache_local
