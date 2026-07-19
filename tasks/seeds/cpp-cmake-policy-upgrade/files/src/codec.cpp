#include "telemetry/codec.hpp"

#include <string>

namespace telemetry {

std::string encode(std::span<const Sample> samples) {
    std::string output;
    for (const Sample &sample : samples) {
        if (!output.empty()) {
            output.push_back(';');
        }
        output += std::to_string(sample.channel);
        output.push_back('=');
        output += std::to_string(sample.value);
    }
    return output;
}

} // namespace telemetry
