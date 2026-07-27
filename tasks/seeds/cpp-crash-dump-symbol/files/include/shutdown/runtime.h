#pragma once

#include <memory>

#include "shutdown/event_loop.h"
#include "shutdown/session.h"

namespace shutdown {

class Runtime {
 public:
  explicit Runtime(AuditLog& audit);
  ~Runtime() = default;

  Runtime(const Runtime&) = delete;
  Runtime& operator=(const Runtime&) = delete;

  void queue_completion(int result);
  void stop() noexcept;
  std::weak_ptr<Session> observe_session() const noexcept;

 private:
  EventLoop loop_;
  std::shared_ptr<Session> session_;
};

}  // namespace shutdown
