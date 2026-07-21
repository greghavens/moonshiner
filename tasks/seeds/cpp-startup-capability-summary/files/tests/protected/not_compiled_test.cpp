#include "orbit/application.hpp"

#include <cstdlib>
#include <iostream>
#include <string>
#include <string_view>

namespace {

class ForbiddenProbe final : public orbit::DeviceProbe {
 public:
  orbit::AcceleratorProbeResult probe_accelerator() override {
    ++calls;
    return {};
  }

  int calls = 0;
};

class ForbiddenAccelerator final : public orbit::AcceleratorRuntime {
 public:
  bool initialize(orbit::AcceleratorDevice&) override {
    ++calls;
    return true;
  }

  bool active() const noexcept override { return false; }
  int calls = 0;
};

class ForbiddenTelemetry final : public orbit::TelemetryRuntime {
 public:
  bool initialize(std::string_view) override {
    ++calls;
    return true;
  }

  bool active() const noexcept override { return false; }
  int calls = 0;
};

class CapturingLog final : public orbit::StartupLog {
 public:
  void write(std::string_view message) override { line = std::string(message); }
  std::string line;
};

}  // namespace

int main() {
  ForbiddenProbe probe;
  ForbiddenAccelerator accelerator;
  ForbiddenTelemetry telemetry;
  CapturingLog log;
  orbit::Application app(probe, accelerator, telemetry, log);

  app.start({true, "/must/not/appear"});

  const std::string expected =
      "startup.capabilities accelerator={compiled:false,runtime:disabled,"
      "reason:not-compiled}; telemetry={compiled:false,runtime:disabled,"
      "reason:not-compiled}";
  if (probe.calls != 0 || accelerator.calls != 0 || telemetry.calls != 0 ||
      log.line != expected) {
    std::cerr << "not-compiled capabilities were not reported safely\n"
              << "actual: " << log.line << '\n';
    return EXIT_FAILURE;
  }

  std::cout << "not-compiled capability tests passed\n";
  return EXIT_SUCCESS;
}
