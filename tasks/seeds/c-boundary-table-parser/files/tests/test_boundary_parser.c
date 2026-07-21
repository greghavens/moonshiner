#include "boundary_parser.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

enum ending {
    END_MISSING,
    END_NUL,
    END_SEPARATOR
};

static int failures;

static void failf(const char *format, ...)
{
    va_list ap;

    ++failures;
    fputs("FAIL: ", stderr);
    va_start(ap, format);
    vfprintf(stderr, format, ap);
    va_end(ap);
    fputc('\n', stderr);
}

static const char *ending_name(enum ending ending)
{
    switch (ending) {
    case END_MISSING:
        return "missing";
    case END_NUL:
        return "NUL";
    case END_SEPARATOR:
        return "separator";
    }
    return "unknown";
}

static void check_boundary_case(size_t payload_len, enum ending ending,
                                size_t scratch_cap)
{
    static const unsigned char payload[] = {'A', 'b', '3', 'z'};
    unsigned char input_box[8];
    unsigned char input_before[sizeof input_box];
    unsigned char scratch_box[12];
    unsigned char expected_scratch[sizeof scratch_box];
    char *input = (char *)&input_box[1];
    char *scratch = (char *)&scratch_box[3];
    const char *sentinel = "output must remain unchanged";
    btab_cell cell = {sentinel, 99u, -1};
    btab_status expected_status;
    btab_status actual;
    size_t input_len;
    size_t i;
    int expected_borrowed = 0;

    memset(input_box, 0xd7, sizeof input_box);
    memcpy(input, payload, payload_len);
    input[payload_len] = ending == END_NUL ? '\0' : '|';
    input_len = ending == END_MISSING ? payload_len : payload_len + 1u;
    memcpy(input_before, input_box, sizeof input_box);

    memset(scratch_box, 0xa5, sizeof scratch_box);
    memcpy(expected_scratch, scratch_box, sizeof scratch_box);

    if (ending == END_MISSING) {
        expected_status = BTAB_INCOMPLETE;
    } else if (ending == END_NUL) {
        expected_status = BTAB_OK;
        expected_borrowed = 1;
    } else if (scratch_cap <= payload_len) {
        expected_status = BTAB_NOSPACE;
    } else {
        expected_status = BTAB_OK;
        memcpy(&expected_scratch[3], payload, payload_len);
        expected_scratch[3 + payload_len] = '\0';
    }

    actual = btab_parse_cell(input, input_len, '|', scratch, scratch_cap,
                             &cell);

    if (actual != expected_status) {
        failf("len=%zu ending=%s cap=%zu: status %d, expected %d",
              payload_len, ending_name(ending), scratch_cap, (int)actual,
              (int)expected_status);
    }

    if (memcmp(input_box, input_before, sizeof input_box) != 0) {
        failf("len=%zu ending=%s cap=%zu: input or input canary changed",
              payload_len, ending_name(ending), scratch_cap);
    }

    if (expected_status != BTAB_OK) {
        if (cell.data != sentinel || cell.length != 99u ||
            cell.borrowed != -1) {
            failf("len=%zu ending=%s cap=%zu: output changed on failure",
                  payload_len, ending_name(ending), scratch_cap);
        }
    } else {
        const char *expected_data = expected_borrowed ? input : scratch;

        if (cell.data != expected_data || cell.length != payload_len ||
            cell.borrowed != expected_borrowed) {
            failf("len=%zu ending=%s cap=%zu: wrong output descriptor",
                  payload_len, ending_name(ending), scratch_cap);
        } else {
            if (memcmp(cell.data, payload, payload_len) != 0) {
                failf("len=%zu ending=%s cap=%zu: payload mismatch",
                      payload_len, ending_name(ending), scratch_cap);
            }
            if (cell.data[payload_len] != '\0') {
                failf("len=%zu ending=%s cap=%zu: result is not terminated",
                      payload_len, ending_name(ending), scratch_cap);
            }
        }
    }

    if (memcmp(scratch_box, expected_scratch, sizeof scratch_box) != 0) {
        failf("len=%zu ending=%s cap=%zu: scratch/canary write outside contract",
              payload_len, ending_name(ending), scratch_cap);
        for (i = 0; i < sizeof scratch_box; ++i) {
            if (scratch_box[i] != expected_scratch[i]) {
                fprintf(stderr,
                        "      first changed offset %zu: 0x%02x, expected 0x%02x\n",
                        i, (unsigned int)scratch_box[i],
                        (unsigned int)expected_scratch[i]);
                break;
            }
        }
    }
    if (scratch_box[3 + scratch_cap] != 0xa5) {
        failf("len=%zu ending=%s cap=%zu: one-past-capacity canary changed",
              payload_len, ending_name(ending), scratch_cap);
    }
}

static void test_boundary_table(void)
{
    static const size_t lengths[] = {0u, 1u, 4u};
    static const enum ending endings[] = {
        END_MISSING, END_NUL, END_SEPARATOR
    };
    size_t li;
    size_t ei;

    /*
     * Derived table:
     *   no in-span terminator       -> INCOMPLETE, no writes
     *   in-span NUL                 -> OK/borrowed, any scratch capacity
     *   in-span separator, cap <= L -> NOSPACE, no writes
     *   in-span separator, cap > L  -> OK/copied and NUL-terminated
     */
    for (li = 0; li < sizeof lengths / sizeof lengths[0]; ++li) {
        size_t capacities[3];
        size_t ci;

        capacities[0] = 0u;
        capacities[1] = lengths[li];
        capacities[2] = lengths[li] + 1u;

        for (ei = 0; ei < sizeof endings / sizeof endings[0]; ++ei) {
            for (ci = 0; ci < sizeof capacities / sizeof capacities[0]; ++ci) {
                if (ci != 0 && capacities[ci] == capacities[ci - 1u]) {
                    continue;
                }
                check_boundary_case(lengths[li], endings[ei],
                                    capacities[ci]);
            }
        }
    }
}

static void test_zero_copy_ignores_scratch(void)
{
    const char input[] = "omega";
    btab_cell cell = {NULL, 0u, 0};
    btab_status status;

    status = btab_parse_cell(input, sizeof input, ',', NULL, 0u, &cell);
    if (status != BTAB_OK || cell.data != input || cell.length != 5u ||
        cell.borrowed != 1) {
        failf("NUL-ended input with NULL scratch did not stay zero-copy");
    }
}

static void test_first_terminator_wins(void)
{
    const char separated[] = "red;blue";
    const char nul_first[] = {'r', 'e', 'd', '\0', ';', 'x'};
    char scratch[4] = {'?', '?', '?', '?'};
    btab_cell cell = {NULL, 0u, 0};

    if (btab_parse_cell(separated, sizeof separated, ';', scratch,
                        sizeof scratch, &cell) != BTAB_OK ||
        cell.data != scratch || cell.length != 3u || cell.borrowed != 0 ||
        strcmp(scratch, "red") != 0) {
        failf("separator was not handled as the first terminator");
    }

    memset(scratch, '?', sizeof scratch);
    if (btab_parse_cell(nul_first, sizeof nul_first, ';', scratch,
                        sizeof scratch, &cell) != BTAB_OK ||
        cell.data != nul_first || cell.length != 3u || cell.borrowed != 1) {
        failf("NUL was not handled as the first terminator");
    }
    if (memcmp(scratch, "????", sizeof scratch) != 0) {
        failf("zero-copy first-terminator case touched scratch");
    }
}

static void test_invalid_arguments(void)
{
    const char input[] = "x";
    char scratch[2] = {'L', 'R'};
    btab_cell cell = {input, 7u, 7};

    if (btab_parse_cell(NULL, 0u, ',', scratch, sizeof scratch, &cell) !=
        BTAB_INVALID) {
        failf("NULL input was not rejected");
    }
    if (btab_parse_cell(input, sizeof input, '\0', scratch, sizeof scratch,
                        &cell) != BTAB_INVALID) {
        failf("NUL separator was not rejected");
    }
    if (btab_parse_cell(input, sizeof input, ',', scratch, sizeof scratch,
                        NULL) != BTAB_INVALID) {
        failf("NULL output was not rejected");
    }
    if (scratch[0] != 'L' || scratch[1] != 'R' || cell.data != input ||
        cell.length != 7u || cell.borrowed != 7) {
        failf("invalid calls changed caller-owned state");
    }
}

int main(void)
{
    test_boundary_table();
    test_zero_copy_ignores_scratch();
    test_first_terminator_wins();
    test_invalid_arguments();

    if (failures != 0) {
        fprintf(stderr, "%d test failure%s\n", failures,
                failures == 1 ? "" : "s");
        return 1;
    }

    puts("all boundary parser tests passed");
    return 0;
}
