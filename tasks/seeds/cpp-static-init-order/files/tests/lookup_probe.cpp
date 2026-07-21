#include "catalog/content_type_lookup.h"

#include <iostream>
#include <string_view>

namespace {

void print_lookup(std::string_view extension) {
  const std::string_view result = catalog::lookup_content_type(extension);
  std::cout << extension << '=' << (result.empty() ? "<missing>" : result)
            << '\n';
}

}  // namespace

int main() {
  print_lookup("json");
  print_lookup("txt");
  print_lookup("unknown");
}
