/* Acceptance tests for the single-pass INI scanner (ini.h).
 * Build and run with `make test`.
 *
 * Pinned here: pair extraction (section/key/value/line), quoting and
 * escape decoding, comment and CRLF handling, precise error codes with
 * 1-based line/column positions, and arena/pair-table exhaustion.
 */
#include "mintest.h"

#include "ini.h"

static char storage[1024];
static ini_arena arena;
static ini_pair pairs[16];
static size_t eline, ecol;

static long parse(const char *text) {
    ini_arena_init(&arena, storage, sizeof storage);
    eline = 999;
    ecol = 999;
    return ini_parse(text, &arena, pairs, 16, &eline, &ecol);
}

static void pair_check(size_t i, const char *sec, const char *key,
                       const char *val, size_t line, const char *msg) {
    CHECK_EQ_STR(pairs[i].section, sec, msg);
    CHECK_EQ_STR(pairs[i].key, key, msg);
    CHECK_EQ_STR(pairs[i].value, val, msg);
    CHECK_EQ_INT(pairs[i].line, line, msg);
}

TEST(sections_pairs_and_comments) {
    long n = parse(
        "# deploy manifest\n"
        "region = eu-west-1\n"
        "\n"
        "[db]\n"
        "host = db.internal\n"
        "port = 5432\n"
        "; retries are handled upstream\n"
        "[log]\n"
        "level = debug\n");
    CHECK_EQ_INT(n, 4, "four pairs scanned");
    pair_check(0, "", "region", "eu-west-1", 2, "global pair before [db]");
    pair_check(1, "db", "host", "db.internal", 5, "first db pair");
    pair_check(2, "db", "port", "5432", 6, "second db pair");
    pair_check(3, "log", "level", "debug", 9, "pair under [log]");
    CHECK_EQ_INT(eline, 0, "success zeroes err_line");
    CHECK_EQ_INT(ecol, 0, "success zeroes err_col");
}

TEST(whitespace_is_trimmed_outside_quotes) {
    long n = parse(
        "   [ core ]   \n"
        "  spaced key   =   spaced value  \n"
        "empty =\n"
        "tabbed\t=\tv\n");
    CHECK_EQ_INT(n, 3, "three pairs scanned");
    pair_check(0, "core", "spaced key", "spaced value", 2,
               "outer whitespace trimmed, inner kept");
    pair_check(1, "core", "empty", "", 3, "empty value is allowed");
    pair_check(2, "core", "tabbed", "v", 4, "tabs trim like spaces");
}

TEST(quoted_values_decode_escapes) {
    long n = parse(
        "motd = \"hello \\\"ops\\\"\\n\\tdone\"\n"
        "banner = \"  padded  \"\n"
        "chars = \"a=b;c#d[e]\"\n"
        "slash = \"C:\\\\share\"\n");
    CHECK_EQ_INT(n, 4, "four quoted pairs scanned");
    CHECK_EQ_STR(pairs[0].value, "hello \"ops\"\n\tdone",
                 "escaped quote, newline and tab decode");
    CHECK_EQ_STR(pairs[1].value, "  padded  ",
                 "quotes preserve inner padding");
    CHECK_EQ_STR(pairs[2].value, "a=b;c#d[e]",
                 "grammar characters are plain text inside quotes");
    CHECK_EQ_STR(pairs[3].value, "C:\\share", "escaped backslash decodes");
}

TEST(unquoted_values_keep_punctuation) {
    long n = parse(
        "path = /srv/data;old#1\n"
        "   ; a full-line comment, even indented\n"
        "expr = a=b=c\n");
    CHECK_EQ_INT(n, 2, "two pairs scanned");
    CHECK_EQ_STR(pairs[0].value, "/srv/data;old#1",
                 "semicolon and hash stay in an unquoted value");
    CHECK_EQ_STR(pairs[1].value, "a=b=c",
                 "only the first = splits key from value");
}

TEST(crlf_and_missing_final_newline) {
    long n = parse(
        "[core]\r\n"
        "alpha = one\r\n"
        "omega = last");
    CHECK_EQ_INT(n, 2, "two pairs scanned");
    pair_check(0, "core", "alpha", "one", 2, "CR is stripped before parsing");
    pair_check(1, "core", "omega", "last", 3, "last line needs no newline");
}

TEST(duplicates_are_kept_in_document_order) {
    long n = parse(
        "[a]\n"
        "x = 1\n"
        "[b]\n"
        "x = 2\n"
        "[a]\n"
        "x = 3\n");
    CHECK_EQ_INT(n, 3, "three pairs scanned");
    pair_check(0, "a", "x", "1", 2, "first a.x");
    pair_check(1, "b", "x", "2", 4, "b.x in between");
    pair_check(2, "a", "x", "3", 6, "reopened section, duplicate key kept");
}

TEST(section_errors_carry_line_and_col) {
    CHECK_EQ_INT(parse("ok = 1\n[good]\nx = 1\n[db\n"), INI_ESECTION,
                 "unterminated section header");
    CHECK_EQ_INT(eline, 4, "error on line 4");
    CHECK_EQ_INT(ecol, 1, "column of the opening bracket");

    CHECK_EQ_INT(parse("  []\n"), INI_ESECTION, "empty section name");
    CHECK_EQ_INT(eline, 1, "empty name reported on line 1");
    CHECK_EQ_INT(ecol, 3, "column of the opening bracket");

    CHECK_EQ_INT(parse("[db] junk\n"), INI_ESECTION,
                 "junk after the closing bracket");
    CHECK_EQ_INT(ecol, 6, "column of the junk character");
}

TEST(key_errors_carry_line_and_col) {
    CHECK_EQ_INT(parse("[ok]\n   nope\n"), INI_EKEY,
                 "line without = is an error");
    CHECK_EQ_INT(eline, 2, "error on line 2");
    CHECK_EQ_INT(ecol, 4, "column of the first non-blank character");

    CHECK_EQ_INT(parse(" = value\n"), INI_EKEY, "empty key is an error");
    CHECK_EQ_INT(eline, 1, "empty key reported on line 1");
    CHECK_EQ_INT(ecol, 2, "column of the equals sign");
}

TEST(quote_errors_carry_line_and_col) {
    CHECK_EQ_INT(parse("greet = \"unterminated\n"), INI_EQUOTE,
                 "unterminated quoted value");
    CHECK_EQ_INT(eline, 1, "unterminated quote on line 1");
    CHECK_EQ_INT(ecol, 9, "column of the opening quote");

    CHECK_EQ_INT(parse("greet = \"bad \\q escape\"\n"), INI_EQUOTE,
                 "unknown escape sequence");
    CHECK_EQ_INT(ecol, 14, "column of the backslash");

    CHECK_EQ_INT(parse("greet = \"done\" trailing\n"), INI_ETRAIL,
                 "junk after the closing quote");
    CHECK_EQ_INT(ecol, 16, "column of the junk character");
}

TEST(first_error_wins) {
    CHECK_EQ_INT(parse("a = 1\nbroken line\nb = 2\n[worse\n"), INI_EKEY,
                 "scanning stops at the first error");
    CHECK_EQ_INT(eline, 2, "which is on line 2");
}

TEST(arena_exhaustion_reports_the_line) {
    char tiny[8];
    ini_arena_init(&arena, tiny, sizeof tiny);
    eline = 999;
    ecol = 999;
    CHECK_EQ_INT(ini_parse("server01 = enabled\n", &arena, pairs, 16,
                           &eline, &ecol),
                 INI_ENOMEM, "eight bytes cannot hold the pair");
    CHECK_EQ_INT(eline, 1, "exhaustion points at the line");
    CHECK_EQ_INT(ecol, 0, "exhaustion has no column");
}

TEST(pair_table_exhaustion_reports_the_line) {
    ini_arena_init(&arena, storage, sizeof storage);
    eline = 999;
    ecol = 999;
    CHECK_EQ_INT(ini_parse("a = 1\nb = 2\nc = 3\n", &arena, pairs, 2,
                           &eline, &ecol),
                 INI_ENOMEM, "third pair does not fit");
    CHECK_EQ_INT(eline, 3, "the pair that did not fit");
    CHECK_EQ_INT(ecol, 0, "exhaustion has no column");
}

TEST(bad_arguments_and_empty_inputs) {
    ini_arena_init(&arena, storage, sizeof storage);
    CHECK_EQ_INT(ini_parse(NULL, &arena, pairs, 16, NULL, NULL), INI_EARG,
                 "NULL text rejected");
    CHECK_EQ_INT(ini_parse("a = 1\n", NULL, pairs, 16, NULL, NULL),
                 INI_EARG, "NULL arena rejected");
    CHECK_EQ_INT(ini_parse("a = 1\n", &arena, NULL, 16, NULL, NULL),
                 INI_EARG, "NULL pair table rejected");
    CHECK_EQ_INT(ini_parse("a = 1\n", &arena, pairs, 0, NULL, NULL),
                 INI_EARG, "zero-capacity pair table rejected");
    CHECK_EQ_INT(parse(""), 0, "empty text scans to zero pairs");
    CHECK_EQ_INT(parse("# only\n; comments\n\n"), 0,
                 "comments and blanks scan to zero pairs");
    CHECK_EQ_INT(eline, 0, "zero-pair success zeroes err_line");
    CHECK_EQ_INT(ecol, 0, "zero-pair success zeroes err_col");
}

TEST(null_err_slots_are_allowed) {
    ini_arena_init(&arena, storage, sizeof storage);
    CHECK_EQ_INT(ini_parse("[a\n", &arena, pairs, 16, NULL, NULL),
                 INI_ESECTION, "errors report fine without err slots");
    CHECK_EQ_INT(ini_parse("k = v\n", &arena, pairs, 16, NULL, NULL), 1,
                 "success works fine without err slots");
}

int main(void) {
    RUN(sections_pairs_and_comments);
    RUN(whitespace_is_trimmed_outside_quotes);
    RUN(quoted_values_decode_escapes);
    RUN(unquoted_values_keep_punctuation);
    RUN(crlf_and_missing_final_newline);
    RUN(duplicates_are_kept_in_document_order);
    RUN(section_errors_carry_line_and_col);
    RUN(key_errors_carry_line_and_col);
    RUN(quote_errors_carry_line_and_col);
    RUN(first_error_wins);
    RUN(arena_exhaustion_reports_the_line);
    RUN(pair_table_exhaustion_reports_the_line);
    RUN(bad_arguments_and_empty_inputs);
    RUN(null_err_slots_are_allowed);
    return mt_summary();
}
