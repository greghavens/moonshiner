#include "orbit/application.hpp"

#include <cstdlib>
#include <iostream>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

using orbit::AcceleratorDevice;
using orbit::AcceleratorProbeResult;
using orbit::AcceleratorProbeStatus;

int failures = 0;

void fail(std::string_view message) {
  ++failures;
  std::cerr << "FAIL: " << message << '\n';
}

void expect(bool condition, std::string_view message) {
  if (!condition) {
    fail(message);
  }
}

void expect_equal(const std::vector<std::string>& actual,
                  const std::vector<std::string>& expected,
                  std::string_view message) {
  if (actual == expected) {
    return;
  }

  fail(message);
  std::cerr << "  expected:";
  for (const std::string& value : expected) {
    std::cerr << " [" << value << ']';
  }
  std::cerr << "\n  actual:";
  for (const std::string& value : actual) {
    std::cerr << " [" << value << ']';
  }
  std::cerr << '\n';
}

class RecordingProbe final : public orbit::DeviceProbe {
 public:
  RecordingProbe(std::vector<std::string>& events,
                 AcceleratorProbeResult result)
      : events_(events), result_(std::move(result)) {}

  AcceleratorProbeResult probe_accelerator() override {
    events_.push_back("probe.accelerator");
    ++calls;
    return result_;
  }

  int calls = 0;

 private:
  std::vector<std::string>& events_;
  AcceleratorProbeResult result_;
};

class RecordingAccelerator final : public orbit::AcceleratorRuntime {
 public:
  RecordingAccelerator(std::vector<std::string>& events, bool start_result)
      : events_(events), start_result_(start_result) {}

  bool initialize(AcceleratorDevice&) override {
    events_.push_back("init.accelerator");
    active_ = start_result_;
    return start_result_;
  }

  bool active() const noexcept override { return active_; }

 private:
  std::vector<std::string>& events_;
  bool start_result_;
  bool active_ = false;
};

class RecordingTelemetry final : public orbit::TelemetryRuntime {
 public:
  RecordingTelemetry(std::vector<std::string>& events, bool start_result)
      : events_(events), start_result_(start_result) {}

  bool initialize(std::string_view destination) override {
    events_.push_back("init.telemetry");
    ++initialize_calls;
    seen_destination = std::string(destination);
    active_ = start_result_;
    return start_result_;
  }

  bool active() const noexcept override { return active_; }

  int initialize_calls = 0;
  std::string seen_destination;

 private:
  std::vector<std::string>& events_;
  bool start_result_;
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

void expect_one_line(const RecordingLog& log, std::string_view expected) {
  expect(log.lines.size() == 1, "startup must emit exactly one capability line");
  if (log.lines.size() == 1 && log.lines.front() != expected) {
    fail("startup capability line does not match the stable schema");
  }
}

void successful_start_is_reported_from_the_first_probe() {
  std::vector<std::string> events;
  AcceleratorDevice device{42};
  RecordingProbe probe(
      events,
      {AcceleratorProbeStatus::available,
       &device,
       "/dev/dri/by-path/pci-0000:65:00.0-render"});
  RecordingAccelerator accelerator(events, true);
  RecordingTelemetry telemetry(events, true);
  RecordingLog log(events);
  orbit::Application app(probe, accelerator, telemetry, log);

  app.start({true, "/var/run/orbit/telemetry.sock"});

  expect(probe.calls == 1, "startup must not probe the accelerator twice");
  expect_equal(events,
               {"probe.accelerator",
                "init.accelerator",
                "init.telemetry",
                "log.capabilities"},
               "startup initialization and reporting order changed");
  expect_one_line(
      log,
      "startup.capabilities accelerator={compiled:true,runtime:enabled}; "
      "telemetry={compiled:true,runtime:enabled}");
  if (!log.lines.empty()) {
    expect(log.lines.front().find("0x") == std::string::npos,
           "capability line contains a process-specific address");
    expect(log.lines.front().find("/dev/") == std::string::npos,
           "capability line contains a host-specific device path");
    expect(log.lines.front().find("/var/run/") == std::string::npos,
           "capability line contains a host-specific telemetry destination");
  }
}

void probe_failures_have_stable_reasons() {
  struct Case {
    AcceleratorProbeStatus status;
    std::string_view reason;
  };

  for (const Case& test_case : {
           Case{AcceleratorProbeStatus::no_device, "no-device"},
           Case{AcceleratorProbeStatus::permission_denied,
                "permission-denied"},
           Case{AcceleratorProbeStatus::incompatible_driver,
                "incompatible-driver"},
           Case{AcceleratorProbeStatus::available, "probe-failed"},
       }) {
    std::vector<std::string> events;
    RecordingProbe probe(events,
                         {test_case.status, nullptr, "/private/device/path"});
    RecordingAccelerator accelerator(events, true);
    RecordingTelemetry telemetry(events, true);
    RecordingLog log(events);
    orbit::Application app(probe, accelerator, telemetry, log);

    app.start({true, "collector.internal:4317"});

    expect(probe.calls == 1, "a failed device probe was repeated for logging");
    expect_equal(events,
                 {"probe.accelerator", "init.telemetry", "log.capabilities"},
                 "probe failure changed startup ordering");
    expect_one_line(
        log,
        std::string("startup.capabilities accelerator={compiled:true,"
                    "runtime:disabled,reason:") +
            std::string(test_case.reason) +
            "}; telemetry={compiled:true,runtime:enabled}");
  }
}

void initialization_and_configuration_failures_are_distinguished() {
  {
    std::vector<std::string> events;
    AcceleratorDevice device{7};
    RecordingProbe probe(
        events,
        {AcceleratorProbeStatus::available, &device, "C:\\Device\\Gpu0"});
    RecordingAccelerator accelerator(events, false);
    RecordingTelemetry telemetry(events, false);
    RecordingLog log(events);
    orbit::Application app(probe, accelerator, telemetry, log);

    app.start({true, "https://collector.example.invalid"});

    expect(probe.calls == 1,
           "initialization failure caused a second device probe");
    expect_equal(events,
                 {"probe.accelerator",
                  "init.accelerator",
                  "init.telemetry",
                  "log.capabilities"},
                 "failed initialization changed startup ordering");
    expect_one_line(
        log,
        "startup.capabilities accelerator={compiled:true,runtime:disabled,"
        "reason:initialization-failed}; telemetry={compiled:true,"
        "runtime:disabled,reason:initialization-failed}");
  }

  {
    std::vector<std::string> events;
    AcceleratorDevice device{9};
    RecordingProbe probe(
        events, {AcceleratorProbeStatus::available, &device, "/dev/gpu9"});
    RecordingAccelerator accelerator(events, true);
    RecordingTelemetry telemetry(events, true);
    RecordingLog log(events);
    orbit::Application app(probe, accelerator, telemetry, log);

    app.start({false, "/secret/unused-collector"});

    expect(telemetry.initialize_calls == 0,
           "configuration-disabled telemetry must not initialize");
    expect_one_line(
        log,
        "startup.capabilities accelerator={compiled:true,runtime:enabled}; "
        "telemetry={compiled:true,runtime:disabled,"
        "reason:configuration-disabled}");
  }
}

}  // namespace

int main() {
  successful_start_is_reported_from_the_first_probe();
  probe_failures_have_stable_reasons();
  initialization_and_configuration_failures_are_distinguished();

  if (failures != 0) {
    std::cerr << failures << " protected assertion(s) failed\n";
    return EXIT_FAILURE;
  }
  std::cout << "startup capability tests passed\n";
  return EXIT_SUCCESS;
}
