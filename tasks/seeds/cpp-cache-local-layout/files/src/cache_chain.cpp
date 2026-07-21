#include "cache_chain.hpp"

#include <cstring>
#include <istream>
#include <limits>
#include <ostream>
#include <stdexcept>
#include <unordered_map>
#include <utility>

namespace cache_local {
namespace {

void write_u32(std::ostream& output, std::uint32_t value) {
  for (unsigned shift = 0; shift != 32; shift += 8) {
    output.put(static_cast<char>((value >> shift) & 0xffu));
  }
}

void write_u64(std::ostream& output, std::uint64_t value) {
  for (unsigned shift = 0; shift != 64; shift += 8) {
    output.put(static_cast<char>((value >> shift) & 0xffu));
  }
}

std::uint32_t read_u32(std::istream& input) {
  std::uint32_t value = 0;
  for (unsigned shift = 0; shift != 32; shift += 8) {
    const int byte = input.get();
    if (byte == std::char_traits<char>::eof()) {
      throw std::runtime_error("truncated CacheChain stream");
    }
    value |= static_cast<std::uint32_t>(static_cast<unsigned char>(byte))
             << shift;
  }
  return value;
}

std::uint64_t read_u64(std::istream& input) {
  std::uint64_t value = 0;
  for (unsigned shift = 0; shift != 64; shift += 8) {
    const int byte = input.get();
    if (byte == std::char_traits<char>::eof()) {
      throw std::runtime_error("truncated CacheChain stream");
    }
    value |= static_cast<std::uint64_t>(static_cast<unsigned char>(byte))
             << shift;
  }
  return value;
}

}  // namespace

struct CacheChain::State {
  explicit State(std::vector<Node> input) : nodes(std::move(input)) {
    by_id.reserve(nodes.size());
    for (std::size_t position = 0; position < nodes.size(); ++position) {
      const auto inserted = by_id.emplace(nodes[position].id, position);
      if (!inserted.second) {
        throw std::invalid_argument("duplicate CacheChain node id");
      }
    }
  }

  std::vector<Node> nodes;
  std::unordered_map<std::uint32_t, std::size_t> by_id;
};

CacheChain::CacheChain(std::vector<Node> nodes)
    : state_(std::make_unique<State>(std::move(nodes))) {}

CacheChain::~CacheChain() = default;
CacheChain::CacheChain(CacheChain&&) noexcept = default;
CacheChain& CacheChain::operator=(CacheChain&&) noexcept = default;

std::size_t CacheChain::size() const noexcept { return state_->nodes.size(); }

CacheChain::const_iterator CacheChain::begin() const noexcept {
  return state_->nodes.begin();
}

CacheChain::const_iterator CacheChain::end() const noexcept {
  return state_->nodes.end();
}

TraversalSample CacheChain::traverse(std::uint32_t start_id,
                                     std::size_t step_limit) const {
  TraversalSample sample;
  std::uint32_t current_id = start_id;

  while (sample.steps < step_limit) {
    ++sample.index_reads;
    const auto found = state_->by_id.find(current_id);
    ++sample.index_pointer_chases;
    if (found == state_->by_id.end()) {
      break;
    }

    const Node& node = state_->nodes[found->second];
    sample.sum += node.value;
    ++sample.steps;
    current_id = node.next_id;
  }

  return sample;
}

void CacheChain::serialize(std::ostream& output) const {
  output.write("CLC1", 4);
  if (state_->nodes.size() > std::numeric_limits<std::uint32_t>::max()) {
    throw std::length_error("too many CacheChain nodes to serialize");
  }
  write_u32(output, static_cast<std::uint32_t>(state_->nodes.size()));

  for (const Node& node : state_->nodes) {
    write_u32(output, node.id);
    write_u32(output, node.next_id);
    std::uint64_t bits = 0;
    static_assert(sizeof(bits) == sizeof(node.value), "unexpected double size");
    std::memcpy(&bits, &node.value, sizeof(bits));
    write_u64(output, bits);
  }

  if (!output) {
    throw std::runtime_error("failed to serialize CacheChain");
  }
}

CacheChain CacheChain::deserialize(std::istream& input) {
  char magic[4] = {};
  input.read(magic, sizeof(magic));
  if (!input || std::memcmp(magic, "CLC1", sizeof(magic)) != 0) {
    throw std::runtime_error("invalid CacheChain stream");
  }

  const std::uint32_t count = read_u32(input);
  std::vector<Node> nodes;
  nodes.reserve(count);
  for (std::uint32_t i = 0; i < count; ++i) {
    Node node{};
    node.id = read_u32(input);
    node.next_id = read_u32(input);
    const std::uint64_t bits = read_u64(input);
    std::memcpy(&node.value, &bits, sizeof(bits));
    nodes.push_back(node);
  }
  return CacheChain(std::move(nodes));
}

}  // namespace cache_local
