#include <cstdlib>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

#include "shutdown/runtime.h"

namespace {

using shutdown::AuditLog;

[[noreturn]] void fail(const std::string& message, const AuditLog& actual) {
  std::cerr << "FAIL: " << message << "\naudit:";
  for (const auto& entry : actual) {
    std::cerr << " [" << entry << ']';
  }
  std::cerr << '\n';
  std::exit(1);
}

void expect_log(const AuditLog& actual, const AuditLog& expected,
                const std::string& scenario) {
  if (actual != expected) {
    fail(scenario, actual);
  }
}

void normal_shutdown_drains_before_session_destruction() {
  AuditLog audit;
  {
    shutdown::Runtime runtime(audit);
    const auto observer = runtime.observe_session();
    if (observer.expired()) {
      fail("runtime did not retain its live session", audit);
    }
    runtime.queue_completion(41);
  }

  expect_log(audit,
             {"session:create", "session:completion:41", "loop:destroy",
              "session:destroy"},
             "pending completion did not run in the required lifetime window");
}

void explicit_stop_releases_owner_and_pending_callback_is_safe() {
  AuditLog audit;
  {
    shutdown::Runtime runtime(audit);
    const auto observer = runtime.observe_session();
    runtime.queue_completion(73);
    runtime.stop();
    if (!observer.expired()) {
      fail("EventLoop extended Session lifetime after stop", audit);
    }
  }

  expect_log(audit, {"session:create", "session:destroy", "loop:destroy"},
             "queued callback was not a safe no-op after stop");
}

void runtimes_do_not_share_callback_ownership() {
  AuditLog first;
  AuditLog second;
  {
    shutdown::Runtime runtime_one(first);
    shutdown::Runtime runtime_two(second);
    runtime_one.queue_completion(5);
    runtime_two.queue_completion(8);
  }

  expect_log(first,
             {"session:create", "session:completion:5", "loop:destroy",
              "session:destroy"},
             "first runtime lifecycle was contaminated");
  expect_log(second,
             {"session:create", "session:completion:8", "loop:destroy",
              "session:destroy"},
             "second runtime lifecycle was contaminated");
}

}  // namespace

int main() {
  normal_shutdown_drains_before_session_destruction();
  explicit_stop_releases_owner_and_pending_callback_is_safe();
  runtimes_do_not_share_callback_ownership();
  std::cout << "lifecycle verification passed\n";
}
