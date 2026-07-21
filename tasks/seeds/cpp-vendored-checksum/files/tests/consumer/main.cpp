#include <streamseal/streamseal.h>

#include <stdio.h>

int main() {
    constexpr uint8_t message[] = {
        0x53, 0x65, 0x61, 0x6c, 0x21, 0x00, 0xff
    };
    if (streamseal_abi_version() != 3u) {
        return 1;
    }
    const uint32_t checksum =
        streamseal_checksum(message, sizeof(message), 0x13579bdfu);
    if (printf("%08x\n", (unsigned)checksum) < 0) {
        return 2;
    }
    return 0;
}
