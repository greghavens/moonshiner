#pragma once

#include <functional>
#include <string>
#include <vector>

namespace shutdown {

using AuditLog = std::vector<std::string>;

class EventLoop {
 public:
  using Completion = std::function<void(int)>;

  explicit EventLoop(AuditLog& audit) noexcept;
  ~EventLoop() noexcept;

  EventLoop(const EventLoop&) = delete;
  EventLoop& operator=(const EventLoop&) = delete;

  void set_completion(Completion completion);
  void queue_completion(int result);
  void drain();

 private:
  AuditLog& audit_;
  Completion completion_;
  std::vector<int> pending_;
};

}  // namespace shutdown
