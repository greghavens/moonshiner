#include "telemetry.h"

#include <stdio.h>
#include <string.h>

static int failures;

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) {                                                    \
            fprintf(stderr, "%s:%d: check failed: %s\n", __FILE__, __LINE__, \
                    #condition);                                               \
            ++failures;                                                        \
        }                                                                      \
    } while (0)

static void init_sample(telemetry_sample *sample, unsigned char fill) {
    memset(sample, fill, sizeof(*sample));
    sample->type = UINT8_C(0x21);
    sample->sequence = UINT32_C(0x01020304);
    sample->millivolts = UINT16_C(0x1234);
    sample->status = UINT8_C(0x80);
}

static void test_golden_frame_and_byte_order(void) {
    static const uint8_t expected[TELEMETRY_FRAME_SIZE] = {
        0xA5, 0x21, 0x01, 0x02, 0x03, 0x04, 0x34, 0x12, 0x80, 0xB5, 0xB1
    };
    telemetry_sample sample;
    telemetry_frame frame;

    init_sample(&sample, 0xC3);
    memset(&frame, 0, sizeof(frame));
    CHECK(telemetry_encode(&sample, &frame) == 0);
    CHECK(memcmp(&frame, expected, sizeof(expected)) == 0);
    CHECK(telemetry_frame_checksum_valid(&frame));
}

static void test_host_padding_is_not_protocol_input(void) {
    telemetry_sample first;
    telemetry_sample second;
    telemetry_frame first_frame;
    telemetry_frame second_frame;

    init_sample(&first, 0x00);
    init_sample(&second, 0xFF);
    CHECK(telemetry_encode(&first, &first_frame) == 0);
    CHECK(telemetry_encode(&second, &second_frame) == 0);
    CHECK(memcmp(&first_frame, &second_frame, sizeof(first_frame)) == 0);
}

static void test_additional_golden_frames(void) {
    static const struct {
        telemetry_sample sample;
        uint8_t expected[TELEMETRY_FRAME_SIZE];
    } cases[] = {
        {
            {0x00, 0x00000000, 0x0000, 0x00},
            {0xA5, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xF0, 0x77}
        },
        {
            {0xFF, 0x89ABCDEF, 0xFEDC, 0x7F},
            {0xA5, 0xFF, 0x89, 0xAB, 0xCD, 0xEF, 0xDC, 0xFE, 0x7F, 0x85, 0xAC}
        }
    };

    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        telemetry_frame frame;
        CHECK(telemetry_encode(&cases[i].sample, &frame) == 0);
        CHECK(memcmp(&frame, cases[i].expected, sizeof(frame)) == 0);
        CHECK(telemetry_frame_checksum_valid(&frame));
    }
}

static void test_corruption_is_rejected(void) {
    telemetry_sample sample;
    telemetry_frame frame;

    init_sample(&sample, 0x5A);
    CHECK(telemetry_encode(&sample, &frame) == 0);
    CHECK(telemetry_frame_checksum_valid(&frame));
    frame.sequence_be[2] ^= UINT8_C(0x40);
    CHECK(!telemetry_frame_checksum_valid(&frame));
}

static void test_null_contract(void) {
    telemetry_sample sample;
    telemetry_frame frame;

    init_sample(&sample, 0x00);
    CHECK(telemetry_encode(NULL, &frame) == -1);
    CHECK(telemetry_encode(&sample, NULL) == -1);
    CHECK(!telemetry_frame_checksum_valid(NULL));
}

int main(void) {
    test_golden_frame_and_byte_order();
    test_host_padding_is_not_protocol_input();
    test_additional_golden_frames();
    test_corruption_is_rejected();
    test_null_contract();

    if (failures != 0) {
        fprintf(stderr, "%d test check(s) failed\n", failures);
        return 1;
    }

    puts("checksum layout tests passed");
    return 0;
}
