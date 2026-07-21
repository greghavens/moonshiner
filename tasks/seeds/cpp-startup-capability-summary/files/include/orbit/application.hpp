#pragma once

#include <string>
#include <string_view>

#ifndef ORBIT_ACCELERATOR_COMPILED
#define ORBIT_ACCELERATOR_COMPILED 1
#endif

#ifndef ORBIT_TELEMETRY_COMPILED
#define ORBIT_TELEMETRY_COMPILED 1
#endif

namespace orbit {

enum class AcceleratorProbeStatus {
  available,
  no_device,
  permission_denied,
  incompatible_driver,
};

struct AcceleratorDevice {
  int driver_cookie = 0;
};

struct AcceleratorProbeResult {
  AcceleratorProbeStatus status = AcceleratorProbeStatus::no_device;
  AcceleratorDevice* device = nullptr;

  // Useful to low-level diagnostics, but not stable enough for startup
  // capability reporting.
  std::string diagnostic_path;
};

class DeviceProbe {
 public:
  virtual ~DeviceProbe() = default;
  virtual AcceleratorProbeResult probe_accelerator() = 0;
};

class AcceleratorRuntime {
 public:
  virtual ~AcceleratorRuntime() = default;
  virtual bool initialize(AcceleratorDevice& device) = 0;
  virtual bool active() const noexcept = 0;
};

class TelemetryRuntime {
 public:
  virtual ~TelemetryRuntime() = default;
  virtual bool initialize(std::string_view destination) = 0;
  virtual bool active() const noexcept = 0;
};

class StartupLog {
 public:
  virtual ~StartupLog() = default;
  virtual void write(std::string_view message) = 0;
};

struct StartupOptions {
  bool telemetry_enabled = true;
  std::string telemetry_destination;
};

class Application final {
 public:
  Application(DeviceProbe& device_probe,
              AcceleratorRuntime& accelerator_runtime,
              TelemetryRuntime& telemetry_runtime,
              StartupLog& startup_log) noexcept;

  void start(const StartupOptions& options);

 private:
  void report_startup_capabilities(const StartupOptions& options);

  [[maybe_unused]] DeviceProbe& device_probe_;
  [[maybe_unused]] AcceleratorRuntime& accelerator_runtime_;
  [[maybe_unused]] TelemetryRuntime& telemetry_runtime_;
  StartupLog& startup_log_;
};

}  // namespace orbit
