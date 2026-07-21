#ifndef EMBEDDED_CONTROLLER_HPP
#define EMBEDDED_CONTROLLER_HPP

#include <cstddef>
#include <cstdint>

namespace embedded {

enum class component {
    transport,
    decoder,
    controller
};

class lifecycle_observer {
public:
    virtual ~lifecycle_observer() = default;
    virtual void constructed(component value) noexcept = 0;
    virtual void destroyed(component value) noexcept = 0;
};

class block_allocator {
public:
    virtual ~block_allocator() = default;
    virtual void* allocate(std::size_t size, std::size_t alignment) noexcept = 0;
    virtual void deallocate(void* address,
                            std::size_t size,
                            std::size_t alignment) noexcept = 0;
};

struct controller_config {
    std::uint16_t input_bias;
    std::uint16_t scale;
};

enum class init_error {
    none,
    invalid_configuration,
    out_of_memory
};

namespace detail {
class transport;
class decoder;
}  // namespace detail

class controller;

struct init_result {
    controller* instance;
    init_error error;
};

init_result initialize_controller(const controller_config& config,
                                  block_allocator& allocator,
                                  lifecycle_observer& observer) noexcept;

void shutdown_controller(controller* instance,
                         block_allocator& allocator) noexcept;

class controller final {
public:
    controller(const controller&) = delete;
    controller& operator=(const controller&) = delete;

    std::uint32_t process(std::uint16_t raw_sample) noexcept;
    std::uint32_t processed_samples() const noexcept;

private:
    friend init_result initialize_controller(const controller_config&,
                                             block_allocator&,
                                             lifecycle_observer&) noexcept;
    friend void shutdown_controller(controller*, block_allocator&) noexcept;

    controller(detail::transport* transport,
               detail::decoder* decoder,
               lifecycle_observer& observer) noexcept;
    ~controller() noexcept;

    detail::transport* transport_;
    detail::decoder* decoder_;
    lifecycle_observer* observer_;
    std::uint32_t processed_samples_;
};

}  // namespace embedded

#endif
