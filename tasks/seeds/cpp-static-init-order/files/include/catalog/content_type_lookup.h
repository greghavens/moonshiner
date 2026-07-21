#ifndef CATALOG_CONTENT_TYPE_LOOKUP_H
#define CATALOG_CONTENT_TYPE_LOOKUP_H

#include <string_view>

namespace catalog {

// Returns an empty view when the extension is not registered.
std::string_view lookup_content_type(std::string_view extension) noexcept;

}  // namespace catalog

#endif
