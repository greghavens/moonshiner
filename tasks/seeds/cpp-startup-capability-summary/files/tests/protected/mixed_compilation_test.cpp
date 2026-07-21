#include "orbit/application.hpp"

#include <cstdlib>
#include <iostream>
#include <string>
#include <string_view>
#include <vector>

namespace {

class RecordingProbe final : public orbit::DeviceProbe {
 public:
  RecordingProbe(std::vector<std::string>& events,
                 orbit::AcceleratorDevice& device)
      : events_(events), device_(device) {}

  orbit::AcceleratorProbeResult probe_accelerator() override {
    events_.push_back("probe.accelerator");
    ++calls;
    return {orbit::AcceleratorProbeStatus::available,
            &device_,
            "/private/device/path"};
  }

  int calls = 0;

 private:
  std::vector<std::string>& events_;
  orbit::AcceleratorDevice& device_;
};

class RecordingAccelerator final : public orbit::AcceleratorRuntime {
 public:
  explicit RecordingAccelerator(std::vector<std::string>& events)
      : events_(events) {}

  bool initialize(orbit::AcceleratorDevice&) override {
    events_.push_back("init.accelerator");
    ++calls;
    active_ = true;
    return true;
  }

  bool active() const noexcept override { return active_; }

  int calls = 0;

 private:
  std::vector<std::string>& events_;
  bool active_ = false;
};

class RecordingTelemetry final : public orbit::TelemetryRuntime {
 public:
  explicit RecordingTelemetry(std::vector<std::string>& events)
      : events_(events) {}

  bool initialize(std::string_view destination) override {
    events_.push_back("init.telemetry");
    ++calls;
    seen_destination = std::string(destination);
    active_ = true;
    return true;
  }

  bool active() const noexcept override { return active_; }

  int calls = 0;
  std::string seen_destination;

 private:
  std::vector<std::string>& events_;
  bool active_ = false;
};

class RecordingLog final : public orbit::StartupLog {
 public:
  explicit RecordingLog(std::vector<std::string>& events) : events_(events) {}

  void write(std::string_view message) override {
    events_.push_back("log.capabilities");
    lines.emplace_back(message);
  }

  std::vector<std::string> lines;

 private:
  std::vector<std::string>& events_;
};

}  // namespace

int main() {
  std::vector<std::string> events;
  orbit::AcceleratorDevice device{17};
  RecordingProbe probe(events, device);
  RecordingAccelerator accelerator(events);
  RecordingTelemetry telemetry(events);
  RecordingLog log(events);
  orbit::Application app(probe, accelerator, telemetry, log);

  app.start({true, "/private/telemetry/destination"});

#if ORBIT_ACCELERATOR_COMPILED && !ORBIT_TELEMETRY_COMPILED
  const std::vector<std::string> expected_events{
      "probe.accelerator", "init.accelerator", "log.capabilities"};
  const std::string expected_line =
      "startup.capabilities accelerator={compiled:true,runtime:enabled}; "
      "telemetry={compiled:false,runtime:disabled,reason:not-compiled}";
  const bool calls_match =
      probe.calls == 1 && accelerator.calls == 1 && telemetry.calls == 0;
#elif !ORBIT_ACCELERATOR_COMPILED && ORBIT_TELEMETRY_COMPILED
  const std::vector<std::string> expected_events{
      "init.telemetry", "log.capabilities"};
  const std::string expected_line =
      "startup.capabilities accelerator={compiled:false,runtime:disabled,"
      "reason:not-compiled}; telemetry={compiled:true,runtime:enabled}";
  const bool calls_match =
      probe.calls == 0 && accelerator.calls == 0 && telemetry.calls == 1;
#else
#error "mixed_compilation_test.cpp requires exactly one compiled capability"
#endif

  if (!calls_match || events != expected_events || log.lines.size() != 1 ||
      log.lines.front() != expected_line) {
    std::cerr << "mixed compile-time capabilities were not independent\n";
    return EXIT_FAILURE;
  }

  std::cout << "mixed compilation capability tests passed\n";
  return EXIT_SUCCESS;
}
