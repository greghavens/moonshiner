#include "catalog/content_type_lookup.h"

#include "registry.h"

#include <string>
#include <unordered_map>

namespace catalog {
namespace {

class Registry {
 public:
  void add(std::string_view extension, std::string_view content_type) {
    entries_.emplace(extension, content_type);
  }

  std::string_view find(std::string_view extension) const noexcept {
    const auto entry = entries_.find(std::string(extension));
    return entry == entries_.end() ? std::string_view{} : entry->second;
  }

 private:
  std::unordered_map<std::string, std::string> entries_;
};

// This dynamic initialization races the registrar in builtins.cpp. Before this
// initializer runs the pointer is zero-initialized, so registrations are lost.
Registry* registry = new Registry();

}  // namespace

namespace detail {

void register_content_type(std::string_view extension,
                           std::string_view content_type) {
  if (registry != nullptr) {
    registry->add(extension, content_type);
  }
}

}  // namespace detail

std::string_view lookup_content_type(std::string_view extension) noexcept {
  return registry == nullptr ? std::string_view{} : registry->find(extension);
}

}  // namespace catalog
