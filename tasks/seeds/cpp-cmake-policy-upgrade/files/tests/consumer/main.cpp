#include <telemetry/codec.hpp>

#include <array>
#include <iostream>

int main() {
    const std::array<telemetry::Sample, 3> samples{{
        {7, 120},
        {9, -4},
        {12, 88},
    }};
    std::cout << telemetry::encode(samples) << '\n';
    return 0;
}
