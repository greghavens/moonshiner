/* Acceptance tests for the capture-blob codec (recfile.h).
 * Build and run with `make test`.
 *
 * Wire format under test (all multi-byte fields little-endian):
 *   header: "SR1\0" magic, u8 version=1, u8 reserved=0, u16 count
 *   record: u16 sensor, u32 at, i32 value (two's complement), u8 flags
 *   trailer: u16 CRC-16/CCITT-FALSE over header+records
 */
#include "mintest.h"

#include <limits.h>
#include <stdint.h>

#include "recfile.h"

/* encode of: {7,1000,-305,1} then {40001,4294967295,2147483647,0} */
static const unsigned char TWO[] = {
    0x53, 0x52, 0x31, 0x00, 0x01, 0x00, 0x02, 0x00,
    0x07, 0x00, 0xE8, 0x03, 0x00, 0x00, 0xCF, 0xFE, 0xFF, 0xFF, 0x01,
    0x41, 0x9C, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x7F, 0x00,
    0x7D, 0xAA,
};

/* encode of an empty capture */
static const unsigned char EMPTY[] = {
    0x53, 0x52, 0x31, 0x00, 0x01, 0x00, 0x00, 0x00, 0xC3, 0xA4,
};

/* version byte bumped to 2, CRC recomputed so only the version is wrong */
static const unsigned char V2[] = {
    0x53, 0x52, 0x31, 0x00, 0x02, 0x00, 0x00, 0x00, 0x1F, 0x3F,
};

/* header claims one record but none follow; CRC over the header is valid */
static const unsigned char COUNT_LIE[] = {
    0x53, 0x52, 0x31, 0x00, 0x01, 0x00, 0x01, 0x00, 0xF2, 0x97,
};

TEST(sizes_follow_the_layout) {
    CHECK_EQ_INT(rf_size(0), 10, "empty capture is header plus CRC");
    CHECK_EQ_INT(rf_size(1), 21, "one record adds 11 bytes");
    CHECK_EQ_INT(rf_size(3), 43, "three records");
}

TEST(crc_matches_known_vectors) {
    CHECK_EQ_INT(rf_crc16("123456789", 9), 0x29B1,
                 "CCITT-FALSE check value");
    CHECK_EQ_INT(rf_crc16("", 0), 0xFFFF, "empty input is the init value");
    CHECK_EQ_INT(rf_crc16("\x00", 1), 0xE1F0, "single zero byte");
    CHECK_EQ_INT(rf_crc16("greenhouse", 10), 0xCF54, "ascii sample");
}

TEST(encode_two_records_byte_exact) {
    rf_reading recs[2] = {
        {7, 1000, -305, 1},
        {40001, 4294967295u, 2147483647, 0},
    };
    unsigned char buf[64];
    long n = rf_encode(recs, 2, buf, sizeof buf);
    CHECK_EQ_INT(n, 32, "encode returns rf_size(2)");
    CHECK(memcmp(buf, TWO, sizeof TWO) == 0, "blob matches byte for byte");
    CHECK_EQ_INT(buf[6], 0x02, "count low byte");
    CHECK_EQ_INT(buf[7], 0x00, "count high byte");
    CHECK_EQ_INT(buf[14], 0xCF, "negative value is two's complement LE");
    CHECK_EQ_INT(buf[30], 0x7D, "CRC low byte trails the records");
    CHECK_EQ_INT(buf[31], 0xAA, "CRC high byte last");
}

TEST(encode_empty_capture_byte_exact) {
    unsigned char buf[16];
    long n = rf_encode(NULL, 0, buf, sizeof buf);
    CHECK_EQ_INT(n, 10, "empty capture encodes to 10 bytes");
    CHECK(memcmp(buf, EMPTY, sizeof EMPTY) == 0, "empty blob matches");
}

TEST(encode_honors_exact_capacity) {
    rf_reading recs[2] = {
        {7, 1000, -305, 1},
        {40001, 4294967295u, 2147483647, 0},
    };
    unsigned char buf[32];
    CHECK_EQ_INT(rf_encode(recs, 2, buf, 32), 32, "exact fit succeeds");
    CHECK_EQ_INT(rf_encode(recs, 2, buf, 31), RF_EARG,
                 "one byte short is rejected");
}

TEST(decode_the_pinned_blob) {
    rf_reading out[4];
    long n = rf_decode(TWO, sizeof TWO, out, 4);
    CHECK_EQ_INT(n, 2, "two records decoded");
    CHECK_EQ_INT(out[0].sensor, 7, "record 0 sensor");
    CHECK_EQ_INT(out[0].at, 1000, "record 0 timestamp");
    CHECK_EQ_INT(out[0].value, -305, "record 0 negative value");
    CHECK_EQ_INT(out[0].flags, 1, "record 0 flags");
    CHECK_EQ_INT(out[1].sensor, 40001, "record 1 sensor");
    CHECK(out[1].at == 4294967295u, "record 1 timestamp is UINT32_MAX");
    CHECK_EQ_INT(out[1].value, 2147483647, "record 1 value");
    CHECK_EQ_INT(out[1].flags, 0, "record 1 flags");
}

TEST(roundtrip_extreme_values) {
    rf_reading recs[4] = {
        {0, 0, INT32_MIN, 0},
        {65535, 4294967295u, INT32_MAX, 255},
        {1234, 567890, -1, 7},
        {42, 3600, 0, 128},
    };
    unsigned char buf[128];
    rf_reading back[4];
    long n = rf_encode(recs, 4, buf, sizeof buf);
    CHECK_EQ_INT(n, 54, "four records encode to rf_size(4)");
    long m = rf_decode(buf, (size_t)n, back, 4);
    CHECK_EQ_INT(m, 4, "four records decode");
    for (int i = 0; i < 4; i++) {
        CHECK_EQ_INT(back[i].sensor, recs[i].sensor, "sensor round-trips");
        CHECK(back[i].at == recs[i].at, "timestamp round-trips");
        CHECK_EQ_INT(back[i].value, recs[i].value, "value round-trips");
        CHECK_EQ_INT(back[i].flags, recs[i].flags, "flags round-trip");
    }
}

TEST(decode_empty_blob) {
    CHECK_EQ_INT(rf_decode(EMPTY, sizeof EMPTY, NULL, 0), 0,
                 "empty capture decodes to zero records");
}

TEST(reject_truncated_buffers) {
    CHECK_EQ_INT(rf_decode(TWO, 0, NULL, 0), RF_ETRUNC, "zero bytes");
    CHECK_EQ_INT(rf_decode(TWO, 9, NULL, 0), RF_ETRUNC,
                 "shorter than any valid blob");
    CHECK_EQ_INT(rf_decode(TWO, sizeof TWO - 1, NULL, 4), RF_ETRUNC,
                 "one byte missing off the end");
}

TEST(reject_length_that_disagrees_with_count) {
    rf_reading out[4];
    CHECK_EQ_INT(rf_decode(COUNT_LIE, sizeof COUNT_LIE, out, 4), RF_ETRUNC,
                 "header claims a record that is not there");
    unsigned char padded[33];
    memcpy(padded, TWO, sizeof TWO);
    padded[32] = 0x00;
    CHECK_EQ_INT(rf_decode(padded, 33, out, 4), RF_ETRUNC,
                 "trailing garbage byte is rejected");
}

TEST(reject_bad_magic) {
    unsigned char bad[sizeof TWO];
    memcpy(bad, TWO, sizeof TWO);
    bad[0] = 0x54;
    CHECK_EQ_INT(rf_decode(bad, sizeof bad, NULL, 4), RF_EMAGIC,
                 "magic is checked before anything else in the header");
}

TEST(reject_unknown_version) {
    CHECK_EQ_INT(rf_decode(V2, sizeof V2, NULL, 0), RF_EVERSION,
                 "version 2 is not ours even with a valid CRC");
}

TEST(reject_corruption_via_crc) {
    rf_reading out[4];
    unsigned char bad[sizeof TWO];

    memcpy(bad, TWO, sizeof TWO);
    bad[10] ^= 0x01; /* one bit in record 0's timestamp */
    CHECK_EQ_INT(rf_decode(bad, sizeof bad, out, 4), RF_ECRC,
                 "flipped payload bit fails the checksum");

    memcpy(bad, TWO, sizeof TWO);
    bad[31] ^= 0x80; /* damage the stored CRC itself */
    CHECK_EQ_INT(rf_decode(bad, sizeof bad, out, 4), RF_ECRC,
                 "damaged trailer fails the checksum");
}

TEST(reject_bad_arguments) {
    rf_reading one = {1, 2, 3, 4};
    rf_reading out[1];
    unsigned char buf[64];
    CHECK_EQ_INT(rf_encode(NULL, 2, buf, sizeof buf), RF_EARG,
                 "NULL records with nonzero count");
    CHECK_EQ_INT(rf_encode(&one, 1, NULL, 64), RF_EARG, "NULL output");
    CHECK_EQ_INT(rf_encode(&one, (size_t)RF_MAX_RECORDS + 1, buf, sizeof buf),
                 RF_EARG, "over the record cap");
    CHECK_EQ_INT(rf_decode(NULL, 32, out, 1), RF_EARG, "NULL input blob");
    CHECK_EQ_INT(rf_decode(TWO, sizeof TWO, out, 1), RF_EARG,
                 "output room for one record but blob has two");
    CHECK_EQ_INT(rf_decode(TWO, sizeof TWO, NULL, 4), RF_EARG,
                 "NULL output with records present");
}

int main(void) {
    RUN(sizes_follow_the_layout);
    RUN(crc_matches_known_vectors);
    RUN(encode_two_records_byte_exact);
    RUN(encode_empty_capture_byte_exact);
    RUN(encode_honors_exact_capacity);
    RUN(decode_the_pinned_blob);
    RUN(roundtrip_extreme_values);
    RUN(decode_empty_blob);
    RUN(reject_truncated_buffers);
    RUN(reject_length_that_disagrees_with_count);
    RUN(reject_bad_magic);
    RUN(reject_unknown_version);
    RUN(reject_corruption_via_crc);
    RUN(reject_bad_arguments);
    return mt_summary();
}
