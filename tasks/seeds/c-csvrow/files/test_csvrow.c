/* Acceptance tests for csvrow.h / csvrow.c — the in-place CSV record
 * parser. Build and run with `make test`.
 *
 * Fixtures are heap copies of exactly buflen bytes (no convenience NUL
 * at the end) — the parser is length-bounded, not string-based.
 */
#include "mintest.h"
#include "csvrow.h"

#include <stdlib.h>

static char *dup_n(const char *s, size_t n) {
    char *p = malloc(n);
    if (p != NULL)
        memcpy(p, s, n);
    return p;
}

/* Field content check: length plus bytes (spans are not NUL-terminated). */
static void check_span(const char *buf, csv_span sp, const char *want,
                       const char *label) {
    size_t wn = strlen(want);
    CHECK_EQ_INT(sp.len, wn, label);
    if (sp.len == wn && wn > 0)
        CHECK(memcmp(buf + sp.start, want, wn) == 0, label);
}

/* Every span must point inside the consumed record's byte range —
 * that's what "in place, no per-field allocation" means. */
static void check_bounds(const csv_span *f, int nf, size_t rec_start,
                         size_t rec_end, const char *label) {
    for (int i = 0; i < nf; i++) {
        CHECK(f[i].start >= rec_start, label);
        CHECK(f[i].start + f[i].len <= rec_end, label);
    }
}

TEST(plain_three_field_record) {
    const char *src = "PO-1002,widget small,4\n";
    size_t n = strlen(src);
    char *buf = dup_n(src, n);
    csv_span f[8];
    size_t pos = 0;
    int nf = csv_row_parse(buf, n, &pos, f, 8);
    CHECK_EQ_INT(nf, 3, "three fields");
    CHECK_EQ_INT(pos, n, "pos moved past the newline");
    check_span(buf, f[0], "PO-1002", "field 1");
    check_span(buf, f[1], "widget small", "field 2");
    check_span(buf, f[2], "4", "field 3");
    check_bounds(f, nf, 0, pos, "spans stay inside the record");
    CHECK_EQ_INT(csv_row_parse(buf, n, &pos, f, 8), 0,
                 "next call reports end of input");
    free(buf);
}

TEST(quoted_comma_and_crlf_terminator) {
    const char *src = "PO-1003,\"bracket, steel\",12\r\nleftover";
    size_t n = strlen(src);
    char *buf = dup_n(src, n);
    csv_span f[8];
    size_t pos = 0;
    int nf = csv_row_parse(buf, n, &pos, f, 8);
    CHECK_EQ_INT(nf, 3, "three fields");
    CHECK_EQ_INT(pos, 29, "CRLF consumed as one terminator");
    check_span(buf, f[0], "PO-1003", "field 1");
    check_span(buf, f[1], "bracket, steel", "comma inside quotes is data");
    check_span(buf, f[2], "12", "field 3");
    check_bounds(f, nf, 0, pos, "spans stay inside the record");
    free(buf);
}

TEST(doubled_quote_unescapes_in_place) {
    const char *src = "PO-1004,\"5\"\" flange\",1\n";
    size_t n = strlen(src);
    char *buf = dup_n(src, n);
    csv_span f[8];
    size_t pos = 0;
    int nf = csv_row_parse(buf, n, &pos, f, 8);
    CHECK_EQ_INT(nf, 3, "three fields");
    check_span(buf, f[1], "5\" flange", "doubled quote collapses to one");
    check_bounds(f, nf, 0, pos, "unescaped content stays in the record");
    free(buf);
}

TEST(quoted_newline_spans_lines) {
    const char *src = "PO-1005,\"backordered\nuntil June\",2\nnext,row\n";
    size_t n = strlen(src);
    char *buf = dup_n(src, n);
    csv_span f[8];
    size_t pos = 0;
    int nf = csv_row_parse(buf, n, &pos, f, 8);
    CHECK_EQ_INT(nf, 3, "one record despite the inner newline");
    CHECK_EQ_INT(pos, 35, "record ends at the unquoted newline");
    check_span(buf, f[1], "backordered\nuntil June",
               "newline inside quotes is data");
    check_span(buf, f[2], "2", "field after the multi-line one");
    nf = csv_row_parse(buf, n, &pos, f, 8);
    CHECK_EQ_INT(nf, 2, "following record parses");
    check_span(buf, f[0], "next", "next record field 1");
    check_span(buf, f[1], "row", "next record field 2");
    CHECK_EQ_INT(pos, n, "buffer fully consumed");
    free(buf);
}

TEST(quoted_crlf_is_preserved_verbatim) {
    const char *src = "A,\"x\r\ny\",B\n";
    size_t n = strlen(src);
    char *buf = dup_n(src, n);
    csv_span f[4];
    size_t pos = 0;
    int nf = csv_row_parse(buf, n, &pos, f, 4);
    CHECK_EQ_INT(nf, 3, "three fields");
    check_span(buf, f[1], "x\r\ny", "CRLF inside quotes kept as data");
    free(buf);
}

TEST(empty_fields_everywhere) {
    const char *src = ",mid,\n";
    size_t n = strlen(src);
    char *buf = dup_n(src, n);
    csv_span f[4];
    size_t pos = 0;
    int nf = csv_row_parse(buf, n, &pos, f, 4);
    CHECK_EQ_INT(nf, 3, "leading and trailing commas make empty fields");
    check_span(buf, f[0], "", "leading empty field");
    check_span(buf, f[1], "mid", "middle field");
    check_span(buf, f[2], "", "trailing empty field");
    free(buf);

    const char *src2 = "PO-1006,,9\n";
    n = strlen(src2);
    buf = dup_n(src2, n);
    pos = 0;
    nf = csv_row_parse(buf, n, &pos, f, 4);
    CHECK_EQ_INT(nf, 3, "empty middle field counted");
    check_span(buf, f[1], "", "middle field is empty");
    free(buf);

    const char *src3 = "a,\"\",b\n";
    n = strlen(src3);
    buf = dup_n(src3, n);
    pos = 0;
    nf = csv_row_parse(buf, n, &pos, f, 4);
    CHECK_EQ_INT(nf, 3, "quoted empty field counted");
    check_span(buf, f[1], "", "quoted empty field has length 0");
    free(buf);
}

TEST(blank_line_is_one_empty_field) {
    const char *src = "\nnext\n";
    size_t n = strlen(src);
    char *buf = dup_n(src, n);
    csv_span f[4];
    size_t pos = 0;
    int nf = csv_row_parse(buf, n, &pos, f, 4);
    CHECK_EQ_INT(nf, 1, "blank line is a record with one field");
    check_span(buf, f[0], "", "that field is empty");
    CHECK_EQ_INT(pos, 1, "consumed just the newline");
    free(buf);
}

TEST(last_record_without_trailing_newline) {
    const char *src = "PO-1009,\"note: \"\"fragile\"\", stack low\",3";
    size_t n = strlen(src);
    char *buf = dup_n(src, n);
    csv_span f[4];
    size_t pos = 0;
    int nf = csv_row_parse(buf, n, &pos, f, 4);
    CHECK_EQ_INT(nf, 3, "record at end of buffer parses");
    CHECK_EQ_INT(pos, n, "pos lands on buflen");
    check_span(buf, f[1], "note: \"fragile\", stack low",
               "escapes and comma handled at EOF");
    check_span(buf, f[2], "3", "trailing field");
    CHECK_EQ_INT(csv_row_parse(buf, n, &pos, f, 4), 0, "then end of input");
    free(buf);
}

TEST(empty_buffer_and_consumed_buffer_return_zero) {
    csv_span f[2];
    size_t pos = 0;
    char one = 'x'; /* any non-NULL pointer; zero bytes are readable */
    CHECK_EQ_INT(csv_row_parse(&one, 0, &pos, f, 2), 0, "empty buffer");
    CHECK_EQ_INT(pos, 0, "pos untouched at end of input");
}

TEST(lone_cr_is_field_data) {
    const char *src = "a\rb,c\n";
    size_t n = strlen(src);
    char *buf = dup_n(src, n);
    csv_span f[4];
    size_t pos = 0;
    int nf = csv_row_parse(buf, n, &pos, f, 4);
    CHECK_EQ_INT(nf, 2, "lone CR does not end the record");
    check_span(buf, f[0], "a\rb", "CR kept inside the field");

    pos = 0;
    const char *src2 = "qty 4\r";
    size_t n2 = strlen(src2);
    char *buf2 = dup_n(src2, n2);
    nf = csv_row_parse(buf2, n2, &pos, f, 4);
    CHECK_EQ_INT(nf, 1, "CR at end of buffer is data too");
    check_span(buf2, f[0], "qty 4\r", "trailing CR belongs to the field");
    CHECK_EQ_INT(pos, n2, "record consumed to buflen");
    free(buf);
    free(buf2);
}

TEST(quote_not_at_field_start_is_literal) {
    const char *src = "ab\"cd,x\n";
    size_t n = strlen(src);
    char *buf = dup_n(src, n);
    csv_span f[4];
    size_t pos = 0;
    int nf = csv_row_parse(buf, n, &pos, f, 4);
    CHECK_EQ_INT(nf, 2, "two fields");
    check_span(buf, f[0], "ab\"cd", "mid-field quote is plain data");
    free(buf);

    const char *src2 = " \"a\",b\n";
    n = strlen(src2);
    buf = dup_n(src2, n);
    pos = 0;
    nf = csv_row_parse(buf, n, &pos, f, 4);
    CHECK_EQ_INT(nf, 2, "two fields");
    check_span(buf, f[0], " \"a\"", "space then quote stays verbatim");
    free(buf);
}

TEST(unterminated_quote_is_an_error) {
    const char *src = "PO,\"oops\nmore text";
    size_t n = strlen(src);
    char *buf = dup_n(src, n);
    csv_span f[4];
    size_t pos = 0;
    CHECK_EQ_INT(csv_row_parse(buf, n, &pos, f, 4), -1,
                 "quote never closes");
    CHECK_EQ_INT(pos, 0, "pos unchanged on error");
    free(buf);
}

TEST(junk_after_closing_quote_is_an_error) {
    const char *src = "PO,\"ok\"x,3\n";
    size_t n = strlen(src);
    char *buf = dup_n(src, n);
    csv_span f[4];
    size_t pos = 0;
    CHECK_EQ_INT(csv_row_parse(buf, n, &pos, f, 4), -1,
                 "data after the closing quote");
    CHECK_EQ_INT(pos, 0, "pos unchanged on error");
    free(buf);

    const char *src2 = "PO,\"ok\" ,3\n";
    n = strlen(src2);
    buf = dup_n(src2, n);
    pos = 0;
    CHECK_EQ_INT(csv_row_parse(buf, n, &pos, f, 4), -1,
                 "even a space after the quote is junk");
    free(buf);
}

TEST(too_many_fields_is_an_error) {
    const char *src = "a,b,c,d,e\n";
    size_t n = strlen(src);
    char *buf = dup_n(src, n);
    csv_span f[8];
    size_t pos = 0;
    CHECK_EQ_INT(csv_row_parse(buf, n, &pos, f, 4), -1,
                 "five fields into four spans");
    CHECK_EQ_INT(pos, 0, "pos unchanged on overflow");
    free(buf);

    buf = dup_n(src, n);
    pos = 0;
    int nf = csv_row_parse(buf, n, &pos, f, 5);
    CHECK_EQ_INT(nf, 5, "exactly max_fields is fine");
    check_span(buf, f[4], "e", "last field intact");
    free(buf);
}

TEST(field_of_only_a_quote) {
    const char *src = "\"\"\"\"\n";
    size_t n = strlen(src);
    char *buf = dup_n(src, n);
    csv_span f[2];
    size_t pos = 0;
    int nf = csv_row_parse(buf, n, &pos, f, 2);
    CHECK_EQ_INT(nf, 1, "one field");
    check_span(buf, f[0], "\"", "four quotes decode to one");
    free(buf);
}

TEST(walks_a_whole_export) {
    const char *src =
        "sku,note,qty\r\n"
        "BX-04,\"long, with \"\"marks\"\"\",7\r\n"
        "BX-09,\"spans\r\ntwo lines\",1\r\n";
    size_t n = strlen(src);
    char *buf = dup_n(src, n);
    csv_span f[8];
    size_t pos = 0;
    int records = 0;
    int nf;
    size_t rec_start = 0;
    while ((nf = csv_row_parse(buf, n, &pos, f, 8)) > 0) {
        records++;
        CHECK_EQ_INT(nf, 3, "every record has three fields");
        check_bounds(f, nf, rec_start, pos, "spans confined per record");
        if (records == 2)
            check_span(buf, f[1], "long, with \"marks\"", "record 2 note");
        if (records == 3)
            check_span(buf, f[1], "spans\r\ntwo lines", "record 3 note");
        rec_start = pos;
    }
    CHECK_EQ_INT(nf, 0, "loop ends cleanly, not with an error");
    CHECK_EQ_INT(records, 3, "three records in the export");
    CHECK_EQ_INT(pos, n, "entire buffer consumed");
    free(buf);
}

int main(void) {
    RUN(plain_three_field_record);
    RUN(quoted_comma_and_crlf_terminator);
    RUN(doubled_quote_unescapes_in_place);
    RUN(quoted_newline_spans_lines);
    RUN(quoted_crlf_is_preserved_verbatim);
    RUN(empty_fields_everywhere);
    RUN(blank_line_is_one_empty_field);
    RUN(last_record_without_trailing_newline);
    RUN(empty_buffer_and_consumed_buffer_return_zero);
    RUN(lone_cr_is_field_data);
    RUN(quote_not_at_field_start_is_literal);
    RUN(unterminated_quote_is_an_error);
    RUN(junk_after_closing_quote_is_an_error);
    RUN(too_many_fields_is_an_error);
    RUN(field_of_only_a_quote);
    RUN(walks_a_whole_export);
    return mt_summary();
}
