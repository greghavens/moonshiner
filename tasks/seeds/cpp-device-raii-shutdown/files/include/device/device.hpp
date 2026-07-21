#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

namespace device {

using ChannelHandle = std::uint32_t;
using CallbackToken = std::uint32_t;

enum class ShutdownOperation {
  unregister_callback,
  close_channel,
};

struct ShutdownError {
  ShutdownOperation operation;
  ChannelHandle handle;
  std::string message;
};

class ShutdownErrorSink {
 public:
  virtual ~ShutdownErrorSink() = default;
  virtual void report(ShutdownError error) noexcept = 0;
};

class DeviceDriver {
 public:
  virtual ~DeviceDriver() = default;

  virtual ChannelHandle open_channel(std::string_view name) = 0;
  virtual CallbackToken register_callback(ChannelHandle handle) = 0;
  virtual void unregister_callback(CallbackToken token) = 0;
  virtual void close_channel(ChannelHandle handle) = 0;
};

class Device final {
 public:
  static std::unique_ptr<Device> open(
      DeviceDriver& driver,
      ShutdownErrorSink& error_sink,
      const std::vector<std::string>& channel_names);

  ~Device() noexcept;

  Device(const Device&) = delete;
  Device& operator=(const Device&) = delete;
  Device(Device&&) = delete;
  Device& operator=(Device&&) = delete;

  const std::vector<ChannelHandle>& handles() const noexcept;
  void close() noexcept;

 private:
  Device(DeviceDriver& driver, ShutdownErrorSink& error_sink) noexcept;

  DeviceDriver& driver_;
  ShutdownErrorSink& error_sink_;
  std::vector<ChannelHandle> handles_;
  std::vector<CallbackToken> callbacks_;
  bool closed_ = false;
};

}  // namespace device
