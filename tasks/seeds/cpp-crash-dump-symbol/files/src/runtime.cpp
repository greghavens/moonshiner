#include "shutdown/runtime.h"

#include <memory>

namespace shutdown {

Runtime::Runtime(AuditLog& audit)
    : loop_(audit), session_(std::make_shared<Session>(audit)) {
  Session* const session = session_.get();
  loop_.set_completion(
      [session](const int result) { session->on_completion(result); });
}

void Runtime::queue_completion(int result) {
  loop_.queue_completion(result);
}

void Runtime::stop() noexcept {
  session_.reset();
}

std::weak_ptr<Session> Runtime::observe_session() const noexcept {
  return session_;
}

}  // namespace shutdown
