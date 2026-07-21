#include "owner_adapter.hpp"
#include "owner_api.h"

#include <cstddef>
#include <cstdint>
#include <iostream>
#include <sstream>
#include <string>
#include <type_traits>
#include <vector>

using CreateSignature =
    owner_status (*)(const owner_resource_ops*, owner_session**);
using DestroySignature = void (*)(owner_session*);
using ViewSignature = owner_resource (*)(const owner_session*);

static_assert(std::is_same_v<decltype(&owner_session_create), CreateSignature>);
static_assert(std::is_same_v<decltype(&owner_session_destroy), DestroySignature>);
static_assert(std::is_same_v<decltype(&owner_session_device), ViewSignature>);
static_assert(std::is_same_v<decltype(&owner_session_channel), ViewSignature>);
static_assert(std::is_standard_layout_v<owner_resource_ops>);
static_assert(offsetof(owner_resource_ops, context) <
              offsetof(owner_resource_ops, create_device));
static_assert(offsetof(owner_resource_ops, create_device) <
              offsetof(owner_resource_ops, configure));

namespace {

int failures = 0;

void check(bool condition, const std::string& message) {
    if (!condition) {
        ++failures;
        std::cerr << "FAIL: " << message << '\n';
    }
}

std::string render(const std::vector<std::string>& events) {
    std::ostringstream output;
    for (std::size_t index = 0; index < events.size(); ++index) {
        if (index != 0) {
            output << ", ";
        }
        output << events[index];
    }
    return output.str();
}

void check_events(
    const std::vector<std::string>& actual,
    const std::vector<std::string>& expected,
    const std::string& scenario) {
    check(
        actual == expected,
        scenario + " events: expected [" + render(expected) + "], got [" +
            render(actual) + "]");
}

struct Token {
    bool live = false;
};

struct Fixture {
    Token device;
    Token channel;
    bool provide_device = true;
    bool provide_channel = true;
    bool configure_succeeds = true;
    int duplicate_destroys = 0;
    std::vector<std::string> events;

    owner_resource_ops operations() {
        return owner_resource_ops{
            static_cast<std::uint32_t>(sizeof(owner_resource_ops)),
            this,
            &Fixture::create_device,
            &Fixture::destroy_device,
            &Fixture::create_channel,
            &Fixture::destroy_channel,
            &Fixture::configure};
    }

    static owner_resource create_device(void* context) {
        auto& fixture = *static_cast<Fixture*>(context);
        fixture.events.emplace_back("create-device");
        if (!fixture.provide_device) {
            return nullptr;
        }
        fixture.device.live = true;
        return &fixture.device;
    }

    static void destroy_device(void* context, owner_resource resource) {
        auto& fixture = *static_cast<Fixture*>(context);
        fixture.events.emplace_back("destroy-device");
        check(resource == &fixture.device, "device callback receives its token");
        if (!fixture.device.live) {
            ++fixture.duplicate_destroys;
        }
        fixture.device.live = false;
    }

    static owner_resource create_channel(
        void* context, owner_resource device_resource) {
        auto& fixture = *static_cast<Fixture*>(context);
        fixture.events.emplace_back("create-channel");
        check(
            device_resource == &fixture.device,
            "channel acquisition receives borrowed device identity");
        if (!fixture.provide_channel) {
            return nullptr;
        }
        fixture.channel.live = true;
        return &fixture.channel;
    }

    static void destroy_channel(void* context, owner_resource resource) {
        auto& fixture = *static_cast<Fixture*>(context);
        fixture.events.emplace_back("destroy-channel");
        check(resource == &fixture.channel, "channel callback receives its token");
        if (!fixture.channel.live) {
            ++fixture.duplicate_destroys;
        }
        fixture.channel.live = false;
    }

    static int configure(
        void* context,
        owner_resource device_resource,
        owner_resource channel_resource) {
        auto& fixture = *static_cast<Fixture*>(context);
        fixture.events.emplace_back("configure");
        check(
            device_resource == &fixture.device,
            "configure receives borrowed device identity");
        check(
            channel_resource == &fixture.channel,
            "configure receives borrowed channel identity");
        return fixture.configure_succeeds ? 1 : 0;
    }
};

void successful_session_uses_adapters_and_defers_cleanup() {
    Fixture fixture;
    owner_resource_ops operations = fixture.operations();
    owner_adapter::Handle* session = nullptr;

    check(
        owner_adapter::create(operations, &session) == OWNER_OK,
        "adapter create reports success");
    check(session != nullptr, "successful create returns a session");
    check(fixture.device.live && fixture.channel.live,
          "both resources remain live while the session exists");
    check(fixture.duplicate_destroys == 0, "no duplicate destroy before release");
    check(owner_adapter::device(session) == &fixture.device,
          "adapter returns the original device identity");
    check(owner_adapter::channel(session) == &fixture.channel,
          "adapter returns the original channel identity");
    check_events(
        fixture.events,
        {"create-device", "create-channel", "configure"},
        "live session");

    owner_adapter::destroy(session);
    check(!fixture.device.live && !fixture.channel.live,
          "destroy releases both resources");
    check(fixture.duplicate_destroys == 0,
          "normal destruction invokes each callback once");
    check_events(
        fixture.events,
        {"create-device",
         "create-channel",
         "configure",
         "destroy-channel",
         "destroy-device"},
        "normal destruction");
}

void device_acquisition_failure_destroys_nothing() {
    Fixture fixture;
    fixture.provide_device = false;
    owner_resource_ops operations = fixture.operations();
    owner_session* session = reinterpret_cast<owner_session*>(1);

    check(
        owner_session_create(&operations, &session) == OWNER_DEVICE_ERROR,
        "device acquisition maps to device error");
    check(session == nullptr, "device failure clears output");
    check(fixture.duplicate_destroys == 0, "missing device is never destroyed");
    check_events(fixture.events, {"create-device"}, "device failure");
}

void channel_acquisition_failure_releases_device_once() {
    Fixture fixture;
    fixture.provide_channel = false;
    owner_resource_ops operations = fixture.operations();
    owner_session* session = reinterpret_cast<owner_session*>(1);

    check(
        owner_session_create(&operations, &session) == OWNER_CHANNEL_ERROR,
        "channel acquisition maps to channel error");
    check(session == nullptr, "channel failure clears output");
    check(!fixture.device.live, "channel failure releases the device");
    check(fixture.duplicate_destroys == 0,
          "channel failure destroys the device exactly once");
    check_events(
        fixture.events,
        {"create-device", "create-channel", "destroy-device"},
        "channel failure");
}

void configuration_failure_unwinds_in_reverse_order_once() {
    Fixture fixture;
    fixture.configure_succeeds = false;
    owner_resource_ops operations = fixture.operations();
    owner_session* session = reinterpret_cast<owner_session*>(1);

    check(
        owner_session_create(&operations, &session) == OWNER_CONFIGURE_ERROR,
        "configuration failure preserves status mapping");
    check(session == nullptr, "configuration failure clears output");
    check(!fixture.device.live && !fixture.channel.live,
          "configuration failure releases both resources");
    check(fixture.duplicate_destroys == 0,
          "configuration failure destroys each resource exactly once");
    check_events(
        fixture.events,
        {"create-device",
         "create-channel",
         "configure",
         "destroy-channel",
         "destroy-device"},
        "configuration failure");
}

void invalid_inputs_preserve_contract() {
    Fixture fixture;
    owner_resource_ops operations = fixture.operations();
    owner_session* session = reinterpret_cast<owner_session*>(1);

    check(
        owner_session_create(nullptr, &session) == OWNER_INVALID_ARGUMENT,
        "null operations are rejected");
    check(session == nullptr, "null operations clear output");
    check(
        owner_session_create(&operations, nullptr) == OWNER_INVALID_ARGUMENT,
        "null output is rejected");

    session = reinterpret_cast<owner_session*>(1);
    operations.struct_size = 0;
    check(
        owner_session_create(&operations, &session) == OWNER_INVALID_ARGUMENT,
        "wrong ABI struct size is rejected");
    check(session == nullptr, "wrong struct size clears output");

    operations = fixture.operations();
    operations.destroy_channel = nullptr;
    session = reinterpret_cast<owner_session*>(1);
    check(
        owner_session_create(&operations, &session) == OWNER_INVALID_ARGUMENT,
        "missing callback is rejected before acquisition");
    check(session == nullptr, "missing callback clears output");
    check(fixture.events.empty(), "invalid tables acquire no resources");
    check(owner_session_device(nullptr) == nullptr, "null device view is null");
    check(owner_session_channel(nullptr) == nullptr, "null channel view is null");
    owner_session_destroy(nullptr);
}

}  // namespace

int main() {
    successful_session_uses_adapters_and_defers_cleanup();
    device_acquisition_failure_destroys_nothing();
    channel_acquisition_failure_releases_device_once();
    configuration_failure_unwinds_in_reverse_order_once();
    invalid_inputs_preserve_contract();

    if (failures != 0) {
        std::cerr << failures << " assertion(s) failed\n";
        return 1;
    }
    std::cout << "all owner conversion tests passed\n";
    return 0;
}
