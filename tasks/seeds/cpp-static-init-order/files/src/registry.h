#ifndef CATALOG_SRC_REGISTRY_H
#define CATALOG_SRC_REGISTRY_H

#include <string_view>

namespace catalog::detail {

void register_content_type(std::string_view extension,
                           std::string_view content_type);

}  // namespace catalog::detail

#endif
