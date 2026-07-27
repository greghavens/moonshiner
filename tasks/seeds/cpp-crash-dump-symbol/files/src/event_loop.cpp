#include "shutdown/event_loop.h"

#include <utility>

namespace shutdown {

EventLoop::EventLoop(AuditLog& audit) noexcept : audit_(audit) {}

EventLoop::~EventLoop() noexcept {
  drain();
  audit_.emplace_back("loop:destroy");
}

void EventLoop::set_completion(Completion completion) {
  completion_ = std::move(completion);
}

void EventLoop::queue_completion(int result) {
  pending_.push_back(result);
}

void EventLoop::drain() {
  auto pending = std::move(pending_);
  pending_.clear();
  for (const int result : pending) {
    if (completion_) {
      completion_(result);
    }
  }
}

}  // namespace shutdown
