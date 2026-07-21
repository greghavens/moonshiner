#include "controller.hpp"

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace {

class audit_log final {
public:
    void append(char value) noexcept
    {
        if (length_ < sizeof(entries_) - 1U) {
            entries_[length_] = value;
            ++length_;
            entries_[length_] = '\0';
        } else {
            overflowed_ = true;
        }
    }

    const char* entries() const noexcept
    {
        return entries_;
    }

    bool overflowed() const noexcept
    {
        return overflowed_;
    }

private:
    char entries_[64] = {};
    std::size_t length_ = 0U;
    bool overflowed_ = false;
};

class recording_observer final : public embedded::lifecycle_observer {
public:
    explicit recording_observer(audit_log& audit) noexcept : audit_(&audit) {}

    void constructed(embedded::component value) noexcept override
    {
        audit_->append(component_code(value, true));
    }

    void destroyed(embedded::component value) noexcept override
    {
        audit_->append(component_code(value, false));
    }

private:
    static char component_code(embedded::component value,
                               bool construction) noexcept
    {
        switch (value) {
        case embedded::component::transport:
            return construction ? 'T' : 't';
        case embedded::component::decoder:
            return construction ? 'D' : 'd';
        case embedded::component::controller:
            return construction ? 'C' : 'c';
        }
        return '?';
    }

    audit_log* audit_;
};

class failing_allocator final : public embedded::block_allocator {
public:
    failing_allocator(audit_log& audit, std::size_t fail_on_call) noexcept
        : audit_(&audit), fail_on_call_(fail_on_call)
    {
    }

    void* allocate(std::size_t size, std::size_t alignment) noexcept override
    {
        ++allocation_calls_;
        if (allocation_calls_ == fail_on_call_) {
            audit_->append('X');
            return nullptr;
        }

        audit_->append('A');
        if (live_count_ == block_count || size > block_size ||
            alignment > alignof(block)) {
            allocator_violation_ = true;
            return nullptr;
        }

        allocation_record& record = live_[live_count_];
        record.address = static_cast<void*>(&blocks_[live_count_]);
        record.size = size;
        record.alignment = alignment;
        ++live_count_;
        return record.address;
    }

    void deallocate(void* address,
                    std::size_t size,
                    std::size_t alignment) noexcept override
    {
        audit_->append('F');
        if (live_count_ == 0U) {
            allocator_violation_ = true;
            return;
        }

        const allocation_record& expected = live_[live_count_ - 1U];
        if (address != expected.address || size != expected.size ||
            alignment != expected.alignment) {
            allocator_violation_ = true;
            return;
        }
        --live_count_;
    }

    std::size_t allocation_calls() const noexcept
    {
        return allocation_calls_;
    }

    std::size_t live_count() const noexcept
    {
        return live_count_;
    }

    bool allocator_violation() const noexcept
    {
        return allocator_violation_;
    }

private:
    static constexpr std::size_t block_count = 3U;
    static constexpr std::size_t block_size = 256U;

    struct alignas(64) block {
        std::byte bytes[block_size];
    };

    struct allocation_record {
        void* address;
        std::size_t size;
        std::size_t alignment;
    };

    audit_log* audit_;
    std::size_t fail_on_call_;
    std::size_t allocation_calls_ = 0U;
    std::size_t live_count_ = 0U;
    bool allocator_violation_ = false;
    block blocks_[block_count] = {};
    allocation_record live_[block_count] = {};
};

int failures = 0;

void check(bool condition, const char* message)
{
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", message);
        ++failures;
    }
}

void check_audit(const audit_log& audit,
                 const char* expected,
                 const char* message)
{
    if (audit.overflowed() || std::strcmp(audit.entries(), expected) != 0) {
        std::fprintf(stderr,
                     "FAIL: %s (expected '%s', got '%s')\n",
                     message,
                     expected,
                     audit.entries());
        ++failures;
    }
}

void test_invalid_configuration()
{
    audit_log audit;
    recording_observer observer(audit);
    failing_allocator allocator(audit, 0U);

    const embedded::init_result result =
        embedded::initialize_controller({7U, 0U}, allocator, observer);

    check(result.instance == nullptr, "invalid config must not create controller");
    check(result.error == embedded::init_error::invalid_configuration,
          "invalid config must retain its error code");
    check(allocator.allocation_calls() == 0U,
          "invalid config must not call allocator");
    check(allocator.live_count() == 0U,
          "invalid config must not retain storage");
    check_audit(audit, "", "invalid config must be side-effect free");
}

void test_allocation_failure(std::size_t fail_on_call,
                             const char* expected_audit)
{
    audit_log audit;
    recording_observer observer(audit);
    failing_allocator allocator(audit, fail_on_call);

    const embedded::init_result result =
        embedded::initialize_controller({3U, 4U}, allocator, observer);

    check(result.instance == nullptr,
          "allocation failure must not return a partial controller");
    check(result.error == embedded::init_error::out_of_memory,
          "allocation failure must retain out_of_memory status");
    check(allocator.allocation_calls() == fail_on_call,
          "initialization must stop at the failed allocation");
    check(allocator.live_count() == 0U,
          "allocation failure must release every accepted block");
    check(!allocator.allocator_violation(),
          "unwind must deallocate exact blocks in reverse order");
    check_audit(audit, expected_audit,
                "allocation failure must unwind completed objects exactly");
}

void test_success_and_shutdown()
{
    audit_log audit;
    recording_observer observer(audit);
    failing_allocator allocator(audit, 0U);

    const embedded::init_result result =
        embedded::initialize_controller({3U, 4U}, allocator, observer);

    check(result.instance != nullptr, "valid initialization must succeed");
    check(result.error == embedded::init_error::none,
          "successful initialization must report no error");
    check(allocator.allocation_calls() == 3U,
          "successful initialization must allocate exactly three objects");
    check(allocator.live_count() == 3U,
          "successful initialization must retain three live objects");
    check_audit(audit, "ATADAC",
                "successful construction order must remain unchanged");

    if (result.instance != nullptr) {
        check(result.instance->process(10U) == 52U,
              "processing must apply input bias before scale");
        check(result.instance->process(2U) == 20U,
              "processing result must remain stable across samples");
        check(result.instance->processed_samples() == 2U,
              "processing must retain exact sample accounting");
    }
    check(allocator.allocation_calls() == 3U,
          "successful processing must not allocate");
    check_audit(audit, "ATADAC",
                "successful processing must not change object lifetimes");

    embedded::shutdown_controller(result.instance, allocator);

    check(allocator.live_count() == 0U,
          "shutdown must release every initialized object");
    check(!allocator.allocator_violation(),
          "shutdown must use exact reverse-order deallocation");
    check_audit(audit, "ATADACcFdFtF",
                "shutdown must destroy controller, decoder, then transport");

    embedded::shutdown_controller(nullptr, allocator);
    check_audit(audit, "ATADACcFdFtF", "null shutdown must be a no-op");
}

}  // namespace

int main()
{
    test_invalid_configuration();
    test_allocation_failure(1U, "X");
    test_allocation_failure(2U, "ATXtF");
    test_allocation_failure(3U, "ATADXdFtF");
    test_success_and_shutdown();

    if (failures != 0) {
        std::fprintf(stderr, "%d test assertion(s) failed\n", failures);
        return 1;
    }

    std::puts("controller allocator/unwind tests passed");
    return 0;
}
