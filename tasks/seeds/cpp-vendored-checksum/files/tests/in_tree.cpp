#include <streamseal/streamseal.h>

int main() {
    constexpr uint8_t message[] = {
        0x53, 0x65, 0x61, 0x6c, 0x21, 0x00, 0xff
    };
    if (streamseal_abi_version() != STREAMSEAL_ABI_VERSION) {
        return 1;
    }
    if (streamseal_checksum(message, sizeof(message), 0x13579bdfu)
        != 0x316f6513u) {
        return 2;
    }
    if (streamseal_checksum(nullptr, 1u, 7u) != 0u) {
        return 3;
    }
    return 0;
}
