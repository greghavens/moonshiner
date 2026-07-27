#include "shutdown/session.h"

#include <string>

namespace shutdown {

Session::Session(AuditLog& audit) : audit_(audit) {
  audit_.emplace_back("session:create");
}

Session::~Session() {
  audit_.emplace_back("session:destroy");
}

void Session::on_completion(int result) {
  audit_.push_back("session:completion:" + std::to_string(result));
}

}  // namespace shutdown
