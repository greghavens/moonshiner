/* test_panel.c — wire-contract tests for the operator-panel feeder.
 * Expected frames below are transcribed from a capture of the vendor's
 * own feeder tool talking to the panel (doc rev D, section 4.1).
 */
#include "mintest.h"
#include "panel.h"

#include <string.h>

TEST(severity_table_is_complete)
{
    CHECK_EQ_INT(panel_tag_count(), 6, "the vendor table defines six severities");
    CHECK_EQ_STR(panel_tag_name(PANEL_INFO), "INFO", "severity 0 tag");
    CHECK_EQ_STR(panel_tag_name(PANEL_WARN), "WARN", "severity 1 tag");
    CHECK_EQ_STR(panel_tag_name(PANEL_FAULT), "FAULT", "severity 2 tag");
    CHECK_EQ_STR(panel_tag_name(PANEL_SERVICE), "SERVICE", "severity 3 tag");
    CHECK_EQ_STR(panel_tag_name(PANEL_OVERRIDE), "OVERRIDE", "severity 4 tag");
    CHECK_EQ_STR(panel_tag_name(PANEL_REMOTE), "REMOTE", "severity 5 tag");
    CHECK(panel_tag_name(6) == NULL, "past the last severity is out of range");
    CHECK(panel_tag_name(-1) == NULL, "negative severity is out of range");
}

TEST(tag_lookup_round_trips)
{
    for (int s = 0; s < 6; s++) {
        const char *tag = panel_tag_name(s);
        CHECK(tag != NULL, "every severity has a tag");
        if (tag != NULL)
            CHECK_EQ_INT(panel_severity_from_tag(tag), s,
                         "tag maps back to its severity");
    }
    CHECK_EQ_INT(panel_severity_from_tag("SERVICE"), PANEL_SERVICE,
                 "SERVICE is severity 3 in the vendor table");
    CHECK_EQ_INT(panel_severity_from_tag("FAULTY"), -1, "unknown tag is rejected");
    CHECK_EQ_INT(panel_severity_from_tag(NULL), -1, "NULL tag is rejected");
}

TEST(fault_frame_matches_vendor_capture)
{
    static const unsigned char want[] = {
        0x80, 'F', 'A', 'U', 'L', 'T', ':',
        'P', 'U', 'M', 'P', ' ', '4', ' ', 'L', 'O', 'W', 0x03
    };
    unsigned char buf[64];
    memset(buf, 0xAA, sizeof buf);

    size_t n = panel_build_frame(PANEL_FAULT, "PUMP 4 LOW", buf, sizeof buf);
    CHECK_EQ_INT(n, sizeof want, "FAULT frame length matches the capture");
    CHECK(n == sizeof want && memcmp(buf, want, n) == 0,
          "FAULT frame bytes match the capture");
}

TEST(remote_frame_matches_vendor_capture)
{
    static const unsigned char want[] = {
        0x80, 'R', 'E', 'M', 'O', 'T', 'E', ':',
        'L', 'I', 'N', 'K', ' ', 'O', 'K', 0x03
    };
    unsigned char buf[64];
    memset(buf, 0xAA, sizeof buf);

    size_t n = panel_build_frame(PANEL_REMOTE, "LINK OK", buf, sizeof buf);
    CHECK_EQ_INT(n, sizeof want, "REMOTE frame length matches the capture");
    CHECK(n == sizeof want && memcmp(buf, want, n) == 0,
          "REMOTE frame bytes match the capture");
}

TEST(build_frame_rejects_bad_input)
{
    unsigned char buf[64];
    CHECK_EQ_INT(panel_build_frame(42, "X", buf, sizeof buf), 0,
                 "unknown severity is refused");
    CHECK_EQ_INT(panel_build_frame(-1, "X", buf, sizeof buf), 0,
                 "negative severity is refused");
    CHECK_EQ_INT(panel_build_frame(PANEL_WARN, NULL, buf, sizeof buf), 0,
                 "NULL text is refused");
    /* WARN:X frame is 1 + 4 + 1 + 1 + 1 = 8 bytes; 7 must not fit. */
    CHECK_EQ_INT(panel_build_frame(PANEL_WARN, "X", buf, 7), 0,
                 "a frame that does not fit is refused");
    CHECK_EQ_INT(panel_build_frame(PANEL_WARN, "X", buf, 8), 8,
                 "an exactly-sized buffer is enough");
}

int main(void)
{
    RUN(severity_table_is_complete);
    RUN(tag_lookup_round_trips);
    RUN(fault_frame_matches_vendor_capture);
    RUN(remote_frame_matches_vendor_capture);
    RUN(build_frame_rejects_bad_input);
    return mt_summary();
}
