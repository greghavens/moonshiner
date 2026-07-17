/* test_kvunesc.c — acceptance tests for the config value escape decoder.
 * Positions in these tests are 1-based (line, byte column), exactly what
 * the loader prints when it underlines a bad escape in an error message.
 */
#include "mintest.h"
#include "kvunesc.h"

#include <stdio.h>
#include <string.h>

/* One successful decode: run it and pin every field of the result. */
static void check_ok(const char *src, size_t start_line, size_t start_col,
                     const char *want, size_t want_len, size_t want_consumed,
                     const char *label)
{
    char out[256];
    memset(out, 0x55, sizeof out);
    kv_result r = kv_decode_value(src, start_line, start_col, out, sizeof out);
    CHECK_EQ_INT(r.status, KV_OK, label);
    CHECK_EQ_INT(r.out_len, want_len, label);
    CHECK_EQ_INT(r.consumed, want_consumed, label);
    CHECK_EQ_INT(r.err_line, 0, label);
    CHECK_EQ_INT(r.err_col, 0, label);
    CHECK(memcmp(out, want, want_len) == 0, label);
    CHECK_EQ_INT((unsigned char)out[want_len], 0, label);
}

/* One failed decode: pin status and the reported backslash position. */
static void check_err(const char *src, size_t start_line, size_t start_col,
                      kv_status want_status, size_t want_line, size_t want_col,
                      const char *label)
{
    char out[256];
    memset(out, 0x55, sizeof out);
    kv_result r = kv_decode_value(src, start_line, start_col, out, sizeof out);
    CHECK_EQ_INT(r.status, want_status, label);
    CHECK_EQ_INT(r.err_line, want_line, label);
    CHECK_EQ_INT(r.err_col, want_col, label);
    CHECK_EQ_INT(r.out_len, 0, label);
    CHECK_EQ_INT(r.consumed, 0, label);
    CHECK_EQ_STR(out, "", label);
}

TEST(plain_values_pass_through)
{
    check_ok("", 1, 1, "", 0, 0, "empty value");
    check_ok("eth0", 1, 8, "eth0", 4, 4, "plain word");
    check_ok("spaces stay put", 1, 1, "spaces stay put", 15, 15, "inner spaces");
}

TEST(simple_escapes_decode)
{
    check_ok("a\\tb\\nc", 1, 1, "a\tb\nc", 5, 7, "tab and newline escapes");
    check_ok("C:\\\\new\\\\table.txt", 1, 1, "C:\\new\\table.txt", 16, 18,
             "doubled backslashes give literal backslashes");
    check_ok("\\x41\\x6fK", 1, 1, "AoK", 3, 9, "hex escapes, either case");
    check_ok("\\x00z", 1, 1, "\0z", 2, 5, "hex zero byte lands in the output");
}

TEST(value_ends_at_an_unescaped_newline)
{
    check_ok("abc\nnext=1", 1, 1, "abc", 3, 3, "newline terminates the value");
    check_ok("\nnext=1", 4, 9, "", 0, 0, "immediate newline means empty value");
}

TEST(continuations_join_lines_and_drop_indent)
{
    check_ok("conn \\\n    keepalive", 1, 1, "conn keepalive", 14, 20,
             "continuation drops the newline and the next line's indent");
    check_ok("a\\\n\tb\\\n  c", 1, 1, "abc", 3, 10,
             "two continuations, tab and space indents both dropped");
    check_ok("one\\\ntwo\nrest", 1, 1, "onetwo", 6, 8,
             "value still ends at the first unescaped newline");
}

TEST(hex_sweep_covers_every_byte_value)
{
    char src[8];
    char out[8];
    for (int v = 0; v < 256; v++) {
        snprintf(src, sizeof src, "\\x%02x", v);
        kv_result r = kv_decode_value(src, 1, 1, out, sizeof out);
        CHECK_EQ_INT(r.status, KV_OK, "lowercase hex escape decodes");
        CHECK_EQ_INT(r.out_len, 1, "one byte of output");
        CHECK_EQ_INT(r.consumed, 4, "four bytes of input");
        CHECK_EQ_INT((unsigned char)out[0], v, "byte value round-trips");

        snprintf(src, sizeof src, "\\x%02X", v);
        r = kv_decode_value(src, 1, 1, out, sizeof out);
        CHECK_EQ_INT(r.status, KV_OK, "uppercase hex escape decodes");
        CHECK_EQ_INT((unsigned char)out[0], v, "uppercase value round-trips");
    }
}

TEST(unknown_and_trailing_escapes_are_positioned)
{
    check_err("oops\\qbar", 3, 9, KV_ERR_UNKNOWN_ESCAPE, 3, 13,
              "unknown escape points at its backslash");
    check_err("\\q", 1, 1, KV_ERR_UNKNOWN_ESCAPE, 1, 1,
              "unknown escape at the very start");
    check_err("abc\\", 1, 1, KV_ERR_TRAILING_BACKSLASH, 1, 4,
              "backslash at end of input");
    check_err("abc\\", 5, 40, KV_ERR_TRAILING_BACKSLASH, 5, 43,
              "start_col offsets the reported column");
}

TEST(bad_hex_is_distinguished_from_truncated_hex)
{
    check_err("\\xg7", 2, 5, KV_ERR_BAD_HEX, 2, 5, "non-hex first digit");
    check_err("\\x5q", 1, 1, KV_ERR_BAD_HEX, 1, 1, "non-hex second digit");
    check_err("ab\\x4", 1, 1, KV_ERR_TRUNCATED_HEX, 1, 3,
              "end of input inside a hex escape");
    check_err("ab\\x4\nrest", 1, 1, KV_ERR_TRUNCATED_HEX, 1, 3,
              "end of line inside a hex escape");
    check_err("\\x", 1, 1, KV_ERR_TRUNCATED_HEX, 1, 1, "bare backslash-x");
    check_err("\\x4\\41", 1, 1, KV_ERR_BAD_HEX, 1, 1,
              "hex digits must be literal, immediately after backslash-x");
}

TEST(positions_survive_continuations)
{
    /* Physical layout, value starting at line 7 column 12:
     *   line 7: ...=first \
     *   line 8:    second \
     *   line 9: <TAB>third \qx      <- bad escape, column 8 of line 9 */
    check_err("first \\\n   second \\\n\tthird \\qx", 7, 12,
              KV_ERR_UNKNOWN_ESCAPE, 9, 8,
              "error after two continuations reports the physical position");
    check_err("ok \\\n  \\x9zZ", 2, 6, KV_ERR_BAD_HEX, 3, 3,
              "bad hex on a continued line reports the continued position");
}

TEST(output_buffer_limits_are_enforced)
{
    char out[6];
    kv_result r = kv_decode_value("hello", 1, 1, out, 5);
    CHECK_EQ_INT(r.status, KV_ERR_NOSPACE, "five bytes cannot hold hello plus NUL");
    CHECK_EQ_INT(r.err_line, 0, "no-space reports no position");
    CHECK_EQ_INT(r.err_col, 0, "no-space reports no position");
    CHECK_EQ_STR(out, "", "output is emptied on no-space");

    r = kv_decode_value("hello", 1, 1, out, 6);
    CHECK_EQ_INT(r.status, KV_OK, "six bytes are exactly enough");
    CHECK_EQ_STR(out, "hello", "exact-fit decode");

    r = kv_decode_value("", 1, 1, out, 0);
    CHECK_EQ_INT(r.status, KV_ERR_NOSPACE, "zero-sized buffer cannot hold the NUL");
}

int main(void)
{
    RUN(plain_values_pass_through);
    RUN(simple_escapes_decode);
    RUN(value_ends_at_an_unescaped_newline);
    RUN(continuations_join_lines_and_drop_indent);
    RUN(hex_sweep_covers_every_byte_value);
    RUN(unknown_and_trailing_escapes_are_positioned);
    RUN(bad_hex_is_distinguished_from_truncated_hex);
    RUN(positions_survive_continuations);
    RUN(output_buffer_limits_are_enforced);
    return mt_summary();
}
