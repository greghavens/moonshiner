#include "orbit/application.hpp"

#include <sstream>
#include <string_view>

namespace orbit {
namespace {

std::string_view probe_reason(AcceleratorProbeStatus status) noexcept {
  switch (status) {
    case AcceleratorProbeStatus::available:
      return "initialization-failed";
    case AcceleratorProbeStatus::no_device:
      return "no-device";
    case AcceleratorProbeStatus::permission_denied:
      return "permission-denied";
    case AcceleratorProbeStatus::incompatible_driver:
      return "incompatible-driver";
  }
  return "probe-failed";
}

}  // namespace

Application::Application(DeviceProbe& device_probe,
                         AcceleratorRuntime& accelerator_runtime,
                         TelemetryRuntime& telemetry_runtime,
                         StartupLog& startup_log) noexcept
    : device_probe_(device_probe),
      accelerator_runtime_(accelerator_runtime),
      telemetry_runtime_(telemetry_runtime),
      startup_log_(startup_log) {}

void Application::start(const StartupOptions& options) {
#if ORBIT_ACCELERATOR_COMPILED
  const AcceleratorProbeResult accelerator =
      device_probe_.probe_accelerator();
  if (accelerator.status == AcceleratorProbeStatus::available &&
      accelerator.device != nullptr) {
    accelerator_runtime_.initialize(*accelerator.device);
  }
#endif

#if ORBIT_TELEMETRY_COMPILED
  if (options.telemetry_enabled) {
    telemetry_runtime_.initialize(options.telemetry_destination);
  }
#endif

  report_startup_capabilities(options);
}

void Application::report_startup_capabilities(
    const StartupOptions& options) {
  std::ostringstream summary;
  summary << "startup.capabilities ";

#if ORBIT_ACCELERATOR_COMPILED
  // Discovery was already performed by start(), but the reporter asks again
  // so it can enrich the line with device details.
  const AcceleratorProbeResult accelerator =
      device_probe_.probe_accelerator();
  if (accelerator_runtime_.active()) {
    summary << "accelerator={compiled:true,runtime:enabled,device:"
            << static_cast<const void*>(accelerator.device)
            << ",path:" << accelerator.diagnostic_path << "}";
  } else {
    summary << "accelerator={compiled:true,runtime:disabled,reason:"
            << probe_reason(accelerator.status) << "}";
  }
#else
  summary << "accelerator={compiled:false,runtime:disabled,reason:not-compiled}";
#endif

  summary << "; ";
#if ORBIT_TELEMETRY_COMPILED
  if (!options.telemetry_enabled) {
    summary << "telemetry={compiled:true,runtime:disabled,"
               "reason:configuration-disabled}";
  } else if (!telemetry_runtime_.active()) {
    summary << "telemetry={compiled:true,runtime:disabled,"
               "reason:initialization-failed}";
  } else {
    summary << "telemetry={compiled:true,runtime:enabled}";
  }
#else
  summary << "telemetry={compiled:false,runtime:disabled,reason:not-compiled}";
#endif

  startup_log_.write(summary.str());
}

}  // namespace orbit
