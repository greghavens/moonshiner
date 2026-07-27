#pragma once

#include "shutdown/event_loop.h"

namespace shutdown {

class Session {
 public:
  explicit Session(AuditLog& audit);
  ~Session();

  Session(const Session&) = delete;
  Session& operator=(const Session&) = delete;

  void on_completion(int result);

 private:
  AuditLog& audit_;
};

}  // namespace shutdown
