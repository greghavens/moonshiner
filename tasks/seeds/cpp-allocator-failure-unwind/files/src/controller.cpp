#include "controller.hpp"

#include <new>

namespace embedded {
namespace detail {

class transport final {
public:
    transport(std::uint16_t input_bias,
              lifecycle_observer& observer) noexcept
        : input_bias_(input_bias), observer_(&observer)
    {
        observer_->constructed(component::transport);
    }

    ~transport() noexcept
    {
        observer_->destroyed(component::transport);
    }

    std::uint32_t capture(std::uint16_t raw_sample) const noexcept
    {
        return static_cast<std::uint32_t>(raw_sample) + input_bias_;
    }

private:
    std::uint16_t input_bias_;
    lifecycle_observer* observer_;
};

class decoder final {
public:
    decoder(std::uint16_t scale, lifecycle_observer& observer) noexcept
        : scale_(scale), observer_(&observer)
    {
        observer_->constructed(component::decoder);
    }

    ~decoder() noexcept
    {
        observer_->destroyed(component::decoder);
    }

    std::uint32_t decode(std::uint32_t captured_sample) const noexcept
    {
        return captured_sample * static_cast<std::uint32_t>(scale_);
    }

private:
    std::uint16_t scale_;
    lifecycle_observer* observer_;
};

}  // namespace detail

controller::controller(detail::transport* transport,
                       detail::decoder* decoder,
                       lifecycle_observer& observer) noexcept
    : transport_(transport),
      decoder_(decoder),
      observer_(&observer),
      processed_samples_(0U)
{
    observer_->constructed(component::controller);
}

controller::~controller() noexcept
{
    observer_->destroyed(component::controller);
}

std::uint32_t controller::process(std::uint16_t raw_sample) noexcept
{
    ++processed_samples_;
    return decoder_->decode(transport_->capture(raw_sample));
}

std::uint32_t controller::processed_samples() const noexcept
{
    return processed_samples_;
}

init_result initialize_controller(const controller_config& config,
                                  block_allocator& allocator,
                                  lifecycle_observer& observer) noexcept
{
    if (config.scale == 0U) {
        return {nullptr, init_error::invalid_configuration};
    }

    void* const transport_memory =
        allocator.allocate(sizeof(detail::transport), alignof(detail::transport));
    if (transport_memory == nullptr) {
        return {nullptr, init_error::out_of_memory};
    }
    detail::transport* const transport =
        ::new (transport_memory) detail::transport(config.input_bias, observer);

    void* const decoder_memory =
        allocator.allocate(sizeof(detail::decoder), alignof(detail::decoder));
    if (decoder_memory == nullptr) {
        return {nullptr, init_error::out_of_memory};
    }
    detail::decoder* const decoder =
        ::new (decoder_memory) detail::decoder(config.scale, observer);

    void* const controller_memory =
        allocator.allocate(sizeof(controller), alignof(controller));
    if (controller_memory == nullptr) {
        decoder->~decoder();
        allocator.deallocate(decoder_memory,
                             sizeof(detail::decoder),
                             alignof(detail::decoder));
        return {nullptr, init_error::out_of_memory};
    }

    controller* const instance =
        ::new (controller_memory) controller(transport, decoder, observer);
    return {instance, init_error::none};
}

void shutdown_controller(controller* instance,
                         block_allocator& allocator) noexcept
{
    if (instance == nullptr) {
        return;
    }

    detail::decoder* const decoder = instance->decoder_;
    detail::transport* const transport = instance->transport_;

    instance->~controller();
    allocator.deallocate(instance, sizeof(controller), alignof(controller));

    decoder->~decoder();
    allocator.deallocate(decoder,
                         sizeof(detail::decoder),
                         alignof(detail::decoder));

    transport->~transport();
    allocator.deallocate(transport,
                         sizeof(detail::transport),
                         alignof(detail::transport));
}

}  // namespace embedded
