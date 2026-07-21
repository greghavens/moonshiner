#include "device/device.hpp"

#include <algorithm>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

using device::CallbackToken;
using device::ChannelHandle;
using device::Device;
using device::DeviceDriver;
using device::ShutdownError;
using device::ShutdownErrorSink;
using device::ShutdownOperation;

class RecordingSink final : public ShutdownErrorSink {
 public:
  void report(ShutdownError error) noexcept override {
    errors.push_back(std::move(error));
  }

  std::vector<ShutdownError> errors;
};

class RecordingDriver final : public DeviceDriver {
 public:
  ChannelHandle open_channel(std::string_view name) override {
    const ChannelHandle handle = next_handle_++;
    events.push_back("open:" + std::string(name) + ":" + std::to_string(handle));
    open_handles.insert(handle);
    return handle;
  }

  CallbackToken register_callback(ChannelHandle handle) override {
    events.push_back("register:" + std::to_string(handle));
    ++register_calls_;
    if (register_calls_ == fail_registration_call) {
      throw std::runtime_error("injected registration failure");
    }
    if (open_handles.count(handle) == 0U) {
      throw std::logic_error("callback registered for a closed channel");
    }

    const CallbackToken token = next_token_++;
    callback_channels.emplace(token, handle);
    return token;
  }

  void unregister_callback(CallbackToken token) override {
    events.push_back("unregister:" + std::to_string(token));
    const auto callback = callback_channels.find(token);
    if (callback == callback_channels.end()) {
      throw std::logic_error("unknown callback token");
    }
    if (fail_unregister_tokens.count(token) != 0U) {
      throw std::runtime_error("injected unregister failure");
    }
    if (open_handles.count(callback->second) == 0U) {
      throw std::logic_error("callback outlived its channel");
    }
    callback_channels.erase(callback);
  }

  void close_channel(ChannelHandle handle) override {
    events.push_back("close:" + std::to_string(handle));
    if (fail_close_handles.count(handle) != 0U) {
      throw std::runtime_error("injected close failure");
    }
    const bool has_callback = std::any_of(
        callback_channels.begin(),
        callback_channels.end(),
        [handle](const auto& item) { return item.second == handle; });
    if (has_callback) {
      throw std::logic_error("channel closed while callback is registered");
    }
    if (open_handles.erase(handle) == 0U) {
      throw std::logic_error("channel closed more than once");
    }
  }

  std::vector<std::string> events;
  std::unordered_set<ChannelHandle> open_handles;
  std::unordered_map<CallbackToken, ChannelHandle> callback_channels;
  std::unordered_set<ChannelHandle> fail_close_handles;
  std::unordered_set<CallbackToken> fail_unregister_tokens;
  int fail_registration_call = -1;

 private:
  ChannelHandle next_handle_ = 101;
  CallbackToken next_token_ = 1001;
  int register_calls_ = 0;
};

[[noreturn]] void fail(const std::string& message) {
  std::cerr << "FAIL: " << message << '\n';
  std::exit(1);
}

void expect(bool condition, const std::string& message) {
  if (!condition) {
    fail(message);
  }
}

void expect_events(
    const std::vector<std::string>& actual,
    const std::vector<std::string>& expected,
    const std::string& context) {
  if (actual == expected) {
    return;
  }

  std::cerr << "FAIL: " << context << "\nexpected:\n";
  for (const std::string& event : expected) {
    std::cerr << "  " << event << '\n';
  }
  std::cerr << "actual:\n";
  for (const std::string& event : actual) {
    std::cerr << "  " << event << '\n';
  }
  std::exit(1);
}

void successful_close_unwinds_in_dependency_order() {
  RecordingDriver driver;
  RecordingSink sink;
  auto device = Device::open(driver, sink, {"control", "telemetry"});

  const std::vector<ChannelHandle> handles_before_close = device->handles();
  expect(
      handles_before_close == std::vector<ChannelHandle>({101, 102}),
      "handles must retain their public open order");

  device->close();

  expect_events(
      driver.events,
      {
          "open:control:101",
          "register:101",
          "open:telemetry:102",
          "register:102",
          "unregister:1002",
          "close:102",
          "unregister:1001",
          "close:101",
      },
      "normal close must reverse each callback/channel dependency pair");
  expect(driver.open_handles.empty(), "normal close leaked a channel");
  expect(driver.callback_channels.empty(), "normal close leaked a callback");
  expect(sink.errors.empty(), "normal close unexpectedly reported an error");
  expect(
      device->handles() == handles_before_close,
      "close changed the public handle snapshot");

  const std::size_t event_count = driver.events.size();
  device->close();
  expect(driver.events.size() == event_count, "close is not idempotent");
}

void partial_open_is_rolled_back_without_leaks() {
  RecordingDriver driver;
  RecordingSink sink;
  driver.fail_registration_call = 2;

  bool saw_open_failure = false;
  try {
    (void)Device::open(driver, sink, {"control", "telemetry"});
  } catch (const std::runtime_error& error) {
    saw_open_failure =
        std::string(error.what()) == "injected registration failure";
  }

  expect(saw_open_failure, "Device::open did not preserve the acquisition error");
  expect_events(
      driver.events,
      {
          "open:control:101",
          "register:101",
          "open:telemetry:102",
          "register:102",
          "close:102",
          "unregister:1001",
          "close:101",
      },
      "partial open must unwind exactly the resources that were acquired");
  expect(driver.open_handles.empty(), "partial open leaked a channel");
  expect(driver.callback_channels.empty(), "partial open leaked a callback");
  expect(sink.errors.empty(), "dependency-safe rollback should not report errors");
}

void destructor_performs_dependency_safe_shutdown() {
  RecordingDriver driver;
  RecordingSink sink;

  {
    auto device = Device::open(driver, sink, {"control"});
    expect(
        device->handles() == std::vector<ChannelHandle>({101}),
        "destructor test did not observe the public handle");
  }

  expect_events(
      driver.events,
      {
          "open:control:101",
          "register:101",
          "unregister:1001",
          "close:101",
      },
      "destruction must use the same dependency-safe shutdown path");
  expect(driver.open_handles.empty(), "destruction leaked a channel");
  expect(driver.callback_channels.empty(), "destruction leaked a callback");
  expect(sink.errors.empty(), "destruction unexpectedly reported an error");
}

void cleanup_errors_are_reported_and_do_not_stop_unwind() {
  RecordingDriver driver;
  RecordingSink sink;
  auto device = Device::open(driver, sink, {"control", "telemetry"});
  driver.fail_close_handles.insert(102);

  device->close();

  expect_events(
      driver.events,
      {
          "open:control:101",
          "register:101",
          "open:telemetry:102",
          "register:102",
          "unregister:1002",
          "close:102",
          "unregister:1001",
          "close:101",
      },
      "cleanup must continue after a driver error");
  expect(sink.errors.size() == 1U, "close failure was not reported exactly once");
  expect(
      sink.errors[0].operation == ShutdownOperation::close_channel,
      "reported the wrong shutdown operation");
  expect(sink.errors[0].handle == 102, "reported the wrong channel handle");
  expect(
      sink.errors[0].message == "injected close failure",
      "did not preserve the driver error message");
  expect(
      driver.open_handles.count(101) == 0U,
      "a close error prevented cleanup of an earlier channel");
  expect(driver.callback_channels.empty(), "a close error leaked a callback");

  const std::size_t event_count = driver.events.size();
  device.reset();
  expect(
      driver.events.size() == event_count,
      "destruction retried an already completed close");
  expect(sink.errors.size() == 1U, "destruction reported a close error twice");
}

void unregister_failure_preserves_dependency_and_continues_unwind() {
  RecordingDriver driver;
  RecordingSink sink;
  auto device = Device::open(driver, sink, {"control", "telemetry"});
  driver.fail_unregister_tokens.insert(1002);

  device->close();

  expect_events(
      driver.events,
      {
          "open:control:101",
          "register:101",
          "open:telemetry:102",
          "register:102",
          "unregister:1002",
          "unregister:1001",
          "close:101",
      },
      "failed unregistration must keep its channel open and continue unwind");
  expect(
      sink.errors.size() == 1U,
      "unregister failure was not reported exactly once");
  expect(
      sink.errors[0].operation == ShutdownOperation::unregister_callback,
      "reported the wrong shutdown operation for unregister failure");
  expect(
      sink.errors[0].handle == 102,
      "reported the wrong channel for unregister failure");
  expect(
      sink.errors[0].message == "injected unregister failure",
      "did not preserve the unregister error message");
  expect(
      driver.open_handles == std::unordered_set<ChannelHandle>({102}),
      "unregister failure did not preserve only its dependent channel");
  expect(
      driver.callback_channels.size() == 1U &&
          driver.callback_channels.count(1002) == 1U,
      "unregister failure did not preserve only its callback");

  const std::size_t event_count = driver.events.size();
  device.reset();
  expect(
      driver.events.size() == event_count,
      "destruction retried shutdown after an unregister failure");
  expect(
      sink.errors.size() == 1U,
      "destruction reported an unregister error twice");
}

}  // namespace

int main() {
  successful_close_unwinds_in_dependency_order();
  partial_open_is_rolled_back_without_leaks();
  destructor_performs_dependency_safe_shutdown();
  cleanup_errors_are_reported_and_do_not_stop_unwind();
  unregister_failure_preserves_dependency_and_continues_unwind();
  std::cout << "all device shutdown tests passed\n";
  return 0;
}
