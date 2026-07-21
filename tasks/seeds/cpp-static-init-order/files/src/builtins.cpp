#include "registry.h"

namespace catalog::detail {
namespace {

class BuiltinRegistrar {
 public:
  BuiltinRegistrar() {
    register_content_type("json", "application/json");
    register_content_type("txt", "text/plain");
  }
};

BuiltinRegistrar registrar;

}  // namespace
}  // namespace catalog::detail
