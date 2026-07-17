/* Acceptance tests for tok.h / tok.c — the job-line tokenizer.
 * Build and run with `make test`.
 */
#include "mintest.h"
#include "tok.h"

/* Convenience: pull the next token into buf[64], return the code. */
static int next64(tok_state *st, const char *input, char *buf) {
    return tok_next(st, input, buf, 64);
}

TEST(splits_plain_words) {
    tok_state st;
    char out[64];
    tok_init(&st);
    const char *line = "restart worker-3 now";
    CHECK_EQ_INT(next64(&st, line, out), 1, "first token present");
    CHECK_EQ_STR(out, "restart", "first token");
    CHECK_EQ_INT(next64(&st, line, out), 1, "second token present");
    CHECK_EQ_STR(out, "worker-3", "second token");
    CHECK_EQ_INT(next64(&st, line, out), 1, "third token present");
    CHECK_EQ_STR(out, "now", "third token");
    CHECK_EQ_INT(next64(&st, line, out), 0, "end of input");
    CHECK_EQ_INT(next64(&st, line, out), 0, "still end on repeat call");
}

TEST(handles_mixed_whitespace) {
    tok_state st;
    char out[64];
    tok_init(&st);
    const char *line = "  a\tb\nc  \r ";
    CHECK_EQ_INT(next64(&st, line, out), 1, "token after leading blanks");
    CHECK_EQ_STR(out, "a", "token a");
    CHECK_EQ_INT(next64(&st, line, out), 1, "token after tab");
    CHECK_EQ_STR(out, "b", "token b");
    CHECK_EQ_INT(next64(&st, line, out), 1, "token after newline");
    CHECK_EQ_STR(out, "c", "token c");
    CHECK_EQ_INT(next64(&st, line, out), 0, "trailing blanks reach end");
}

TEST(empty_and_blank_inputs) {
    tok_state st;
    char out[64];
    tok_init(&st);
    out[0] = 'x';
    CHECK_EQ_INT(next64(&st, "", out), 0, "empty string has no tokens");
    CHECK_EQ_STR(out, "", "out is empty string on end");
    tok_init(&st);
    CHECK_EQ_INT(next64(&st, " \t\n ", out), 0, "blank string has no tokens");
}

TEST(single_quotes_keep_everything_literal) {
    tok_state st;
    char out[64];
    tok_init(&st);
    const char *line = "tag 'weekly report.pdf' done";
    CHECK_EQ_INT(next64(&st, line, out), 1, "token 1");
    CHECK_EQ_STR(out, "tag", "verb");
    CHECK_EQ_INT(next64(&st, line, out), 1, "token 2");
    CHECK_EQ_STR(out, "weekly report.pdf", "quoted filename is one token");
    CHECK_EQ_INT(next64(&st, line, out), 1, "token 3");
    CHECK_EQ_STR(out, "done", "trailing word");
    CHECK_EQ_INT(next64(&st, line, out), 0, "end");

    tok_init(&st);
    CHECK_EQ_INT(next64(&st, "'a\\nb'", out), 1, "backslash inside singles");
    CHECK_EQ_STR(out, "a\\nb", "backslash-n stays two literal chars");

    tok_init(&st);
    CHECK_EQ_INT(next64(&st, "'say \"hi\"'", out), 1, "doubles inside singles");
    CHECK_EQ_STR(out, "say \"hi\"", "double quotes are literal in singles");
}

TEST(double_quotes_preserve_inner_spacing) {
    tok_state st;
    char out[64];
    tok_init(&st);
    CHECK_EQ_INT(next64(&st, "\"two  spaces\"", out), 1, "quoted token");
    CHECK_EQ_STR(out, "two  spaces", "inner double space preserved");
}

TEST(double_quote_escapes) {
    tok_state st;
    char out[64];
    tok_init(&st);
    CHECK_EQ_INT(next64(&st, "\"say \\\"hi\\\"\"", out), 1, "escaped quotes");
    CHECK_EQ_STR(out, "say \"hi\"", "backslash-quote becomes a quote");

    tok_init(&st);
    CHECK_EQ_INT(next64(&st, "\"c:\\\\temp\"", out), 1, "escaped backslash");
    CHECK_EQ_STR(out, "c:\\temp", "double backslash becomes one");

    tok_init(&st);
    CHECK_EQ_INT(next64(&st, "\"line\\nbreak\"", out), 1,
                 "backslash before ordinary char");
    CHECK_EQ_STR(out, "line\\nbreak",
                 "backslash kept literally before non-special char");
}

TEST(escapes_outside_quotes) {
    tok_state st;
    char out[64];
    tok_init(&st);
    const char *line = "two\\ words next";
    CHECK_EQ_INT(next64(&st, line, out), 1, "escaped space token");
    CHECK_EQ_STR(out, "two words", "escaped space joins the token");
    CHECK_EQ_INT(next64(&st, line, out), 1, "following token");
    CHECK_EQ_STR(out, "next", "next word");

    tok_init(&st);
    CHECK_EQ_INT(next64(&st, "\\'abc", out), 1, "escaped quote outside");
    CHECK_EQ_STR(out, "'abc", "escaped single quote is literal");
}

TEST(adjacent_segments_concatenate) {
    tok_state st;
    char out[64];
    tok_init(&st);
    CHECK_EQ_INT(next64(&st, "pre\"mid dle\"post", out), 1, "mixed segments");
    CHECK_EQ_STR(out, "premid dlepost", "bare+quoted+bare is one token");

    tok_init(&st);
    CHECK_EQ_INT(next64(&st, "'a'\"b\"c", out), 1, "quote styles back to back");
    CHECK_EQ_STR(out, "abc", "all three segments joined");
}

TEST(empty_quotes_yield_empty_token) {
    tok_state st;
    char out[64];
    tok_init(&st);
    const char *line = "'' \"\"";
    CHECK_EQ_INT(next64(&st, line, out), 1, "empty single-quoted token");
    CHECK_EQ_STR(out, "", "token is the empty string");
    CHECK_EQ_INT(next64(&st, line, out), 1, "empty double-quoted token");
    CHECK_EQ_STR(out, "", "token is the empty string");
    CHECK_EQ_INT(next64(&st, line, out), 0, "then end");

    tok_init(&st);
    char tiny[1];
    CHECK_EQ_INT(tok_next(&st, "''", tiny, 1), 1,
                 "empty token fits in a 1-byte buffer");
    CHECK_EQ_STR(tiny, "", "1-byte buffer holds the terminator");
}

TEST(unterminated_quotes_are_errors) {
    tok_state st;
    char out[64];
    tok_init(&st);
    out[0] = 'x';
    CHECK_EQ_INT(next64(&st, "\"abc", out), -1, "unterminated double quote");
    CHECK_EQ_STR(out, "", "out cleared on error");
    CHECK_EQ_INT(next64(&st, "\"abc", out), -1, "error repeats on retry");

    tok_init(&st);
    CHECK_EQ_INT(next64(&st, "'abc", out), -1, "unterminated single quote");

    tok_init(&st);
    CHECK_EQ_INT(next64(&st, "\"abc\\", out), -1,
                 "backslash then end inside doubles");
}

TEST(trailing_backslash_is_an_error) {
    tok_state st;
    char out[64];
    tok_init(&st);
    CHECK_EQ_INT(next64(&st, "abc\\", out), -1, "dangling escape");
    CHECK_EQ_INT(next64(&st, "abc\\", out), -1, "still an error on retry");
}

TEST(error_midstream_after_good_tokens) {
    tok_state st;
    char out[64];
    tok_init(&st);
    const char *line = "ok 'bad";
    CHECK_EQ_INT(next64(&st, line, out), 1, "first token fine");
    CHECK_EQ_STR(out, "ok", "first token text");
    CHECK_EQ_INT(next64(&st, line, out), -1, "second token unterminated");
    CHECK_EQ_INT(next64(&st, line, out), -1, "state stays at the bad token");
}

TEST(output_buffer_bounds) {
    tok_state st;
    char out[64];
    tok_init(&st);
    const char *line = "abcdef tail";
    char small[4];
    CHECK_EQ_INT(tok_next(&st, line, small, sizeof small), -1,
                 "token longer than buffer is an error");
    CHECK_EQ_STR(small, "", "small buffer cleared on error");
    CHECK_EQ_INT(next64(&st, line, out), 1,
                 "retry with a bigger buffer succeeds");
    CHECK_EQ_STR(out, "abcdef", "same token delivered after retry");
    CHECK_EQ_INT(next64(&st, line, out), 1, "stream continues");
    CHECK_EQ_STR(out, "tail", "next token unaffected");

    tok_init(&st);
    char exact[4];
    CHECK_EQ_INT(tok_next(&st, "abc", exact, sizeof exact), 1,
                 "token of outsz-1 chars fits exactly");
    CHECK_EQ_STR(exact, "abc", "exact-fit token");

    tok_init(&st);
    char shy[3];
    CHECK_EQ_INT(tok_next(&st, "abc", shy, sizeof shy), -1,
                 "outsz-char token does not fit");

    tok_init(&st);
    CHECK_EQ_INT(tok_next(&st, "abc", out, 0), -1, "outsz 0 is an error");
}

TEST(states_are_independent) {
    tok_state a, b;
    char out[64];
    tok_init(&a);
    tok_init(&b);
    const char *la = "alpha beta";
    const char *lb = "one two three";
    CHECK_EQ_INT(next64(&a, la, out), 1, "stream a token 1");
    CHECK_EQ_STR(out, "alpha", "a1");
    CHECK_EQ_INT(next64(&b, lb, out), 1, "stream b token 1");
    CHECK_EQ_STR(out, "one", "b1");
    CHECK_EQ_INT(next64(&a, la, out), 1, "stream a token 2");
    CHECK_EQ_STR(out, "beta", "a2");
    CHECK_EQ_INT(next64(&b, lb, out), 1, "stream b token 2");
    CHECK_EQ_STR(out, "two", "b2");
    CHECK_EQ_INT(next64(&a, la, out), 0, "stream a done");
    CHECK_EQ_INT(next64(&b, lb, out), 1, "stream b token 3");
    CHECK_EQ_STR(out, "three", "b3");
    CHECK_EQ_INT(next64(&b, lb, out), 0, "stream b done");
}

TEST(realistic_job_line) {
    tok_state st;
    char out[64];
    tok_init(&st);
    const char *line =
        "archive 'weekly report.pdf' --dest \"/mnt/backup/july week 2\" -v";
    CHECK_EQ_INT(next64(&st, line, out), 1, "verb");
    CHECK_EQ_STR(out, "archive", "verb text");
    CHECK_EQ_INT(next64(&st, line, out), 1, "filename");
    CHECK_EQ_STR(out, "weekly report.pdf", "filename with space");
    CHECK_EQ_INT(next64(&st, line, out), 1, "flag");
    CHECK_EQ_STR(out, "--dest", "flag text");
    CHECK_EQ_INT(next64(&st, line, out), 1, "path");
    CHECK_EQ_STR(out, "/mnt/backup/july week 2", "quoted path with spaces");
    CHECK_EQ_INT(next64(&st, line, out), 1, "trailing flag");
    CHECK_EQ_STR(out, "-v", "trailing flag text");
    CHECK_EQ_INT(next64(&st, line, out), 0, "end of line");
}

int main(void) {
    RUN(splits_plain_words);
    RUN(handles_mixed_whitespace);
    RUN(empty_and_blank_inputs);
    RUN(single_quotes_keep_everything_literal);
    RUN(double_quotes_preserve_inner_spacing);
    RUN(double_quote_escapes);
    RUN(escapes_outside_quotes);
    RUN(adjacent_segments_concatenate);
    RUN(empty_quotes_yield_empty_token);
    RUN(unterminated_quotes_are_errors);
    RUN(trailing_backslash_is_an_error);
    RUN(error_midstream_after_good_tokens);
    RUN(output_buffer_bounds);
    RUN(states_are_independent);
    RUN(realistic_job_line);
    return mt_summary();
}
