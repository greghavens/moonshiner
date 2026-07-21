#include "device/device.hpp"

#include <exception>
#include <utility>

namespace device {
namespace {

void report_error(
    ShutdownErrorSink& sink,
    ShutdownOperation operation,
    ChannelHandle handle,
    const char* fallback_message) noexcept {
  try {
    throw;
  } catch (const std::exception& error) {
    sink.report(ShutdownError{operation, handle, error.what()});
  } catch (...) {
    sink.report(ShutdownError{operation, handle, fallback_message});
  }
}

}  // namespace

Device::Device(DeviceDriver& driver, ShutdownErrorSink& error_sink) noexcept
    : driver_(driver), error_sink_(error_sink) {}

std::unique_ptr<Device> Device::open(
    DeviceDriver& driver,
    ShutdownErrorSink& error_sink,
    const std::vector<std::string>& channel_names) {
  auto device = std::unique_ptr<Device>(new Device(driver, error_sink));

  try {
    for (const std::string& name : channel_names) {
      const ChannelHandle handle = driver.open_channel(name);
      device->handles_.push_back(handle);
      device->callbacks_.push_back(driver.register_callback(handle));
    }
  } catch (...) {
    device->close();
    throw;
  }

  return device;
}

Device::~Device() noexcept { close(); }

const std::vector<ChannelHandle>& Device::handles() const noexcept {
  return handles_;
}

void Device::close() noexcept {
  if (closed_) {
    return;
  }
  closed_ = true;

  // Channels and callbacks are tracked independently. Closing the channels
  // first leaves the callbacks referring to resources that no longer exist.
  for (std::size_t index = handles_.size(); index > 0; --index) {
    const ChannelHandle handle = handles_[index - 1];
    try {
      driver_.close_channel(handle);
    } catch (...) {
      report_error(
          error_sink_,
          ShutdownOperation::close_channel,
          handle,
          "unknown close error");
    }
  }

  for (std::size_t index = callbacks_.size(); index > 0; --index) {
    const std::size_t owned_index = index - 1;
    try {
      driver_.unregister_callback(callbacks_[owned_index]);
    } catch (...) {
      report_error(
          error_sink_,
          ShutdownOperation::unregister_callback,
          handles_[owned_index],
          "unknown unregister error");
    }
  }
}

}  // namespace device
