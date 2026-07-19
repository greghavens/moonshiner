#pragma once

#include <cstdint>
#include <span>
#include <string>

namespace telemetry {

struct Sample {
    std::uint16_t channel;
    std::int32_t value;
};

std::string encode(std::span<const Sample> samples);

} // namespace telemetry
