/* Differential harness for the assembly board renderer.
 *
 * board_fmt is linked from boardfmt.s. Every render is compared byte for
 * byte against the C reference below, the full checkers starting position
 * is pinned as a literal, and canary bytes right after the 162-byte frame
 * catch any store past the declared length (including a stray NUL).
 */
#include <stdio.h>
#include <string.h>

long board_fmt(const unsigned char *cells, char *out);

static int checks = 0;
static int failures = 0;

#define CHECK(cond, ...)                                                  \
    do {                                                                  \
        checks++;                                                         \
        if (!(cond)) {                                                    \
            failures++;                                                   \
            printf("FAIL line %d: ", __LINE__);                           \
            printf(__VA_ARGS__);                                          \
            printf("\n");                                                 \
        }                                                                 \
    } while (0)

#define FRAME 162
#define GUARD 16
#define CANARY 0x5A

static const char GLYPH[5] = {'.', 'w', 'W', 'b', 'B'};

static long ref_fmt(const unsigned char *cells, char *out) {
    long w = 0;
    for (int r = 0; r < 8; r++) {
        out[w++] = (char)('8' - r);
        for (int c = 0; c < 8; c++) {
            out[w++] = ' ';
            out[w++] = GLYPH[cells[r * 8 + c]];
        }
        out[w++] = '\n';
    }
    out[w++] = ' ';
    for (int c = 0; c < 8; c++) {
        out[w++] = ' ';
        out[w++] = (char)('a' + c);
    }
    out[w++] = '\n';
    return w;
}

/* Render via asm with guards, compare against reference + optional pin. */
static void fmt_checked(const unsigned char *cells, const char *pin,
                        const char *what) {
    char want[FRAME];
    unsigned char snap[64];
    char buf[FRAME + 2 * GUARD];
    memcpy(snap, cells, 64);
    memset(buf, CANARY, sizeof buf);

    long wn = ref_fmt(cells, want);
    CHECK(wn == FRAME, "harness bug: reference frame is %ld bytes", wn);
    long gn = board_fmt(cells, buf + GUARD);

    CHECK(gn == FRAME, "%s: returned %ld want %d", what, gn, FRAME);
    if (memcmp(buf + GUARD, want, FRAME) != 0) {
        CHECK(0, "%s: frame does not match reference", what);
        printf("got:\n%.*s\nwant:\n%.*s\n", FRAME, buf + GUARD, FRAME,
               want);
    } else {
        checks++;
    }
    if (pin)
        CHECK(memcmp(buf + GUARD, pin, FRAME) == 0,
              "%s: frame does not match the pinned literal", what);
    int lo = 1, hi = 1;
    for (int i = 0; i < GUARD; i++) {
        lo &= ((unsigned char)buf[i] == CANARY);
        hi &= ((unsigned char)buf[GUARD + FRAME + i] == CANARY);
    }
    CHECK(lo, "%s: stored before the output buffer", what);
    CHECK(hi, "%s: stored past the 162nd byte (stray NUL?)", what);
    CHECK(memcmp(snap, cells, 64) == 0, "%s: cell array was modified",
          what);
}

static void test_pinned_positions(void) {
    /* Checkers starting position — full frame pinned as a literal. */
    static const char START_PIN[] =
        "8 . b . b . b . b\n"
        "7 b . b . b . b .\n"
        "6 . b . b . b . b\n"
        "5 . . . . . . . .\n"
        "4 . . . . . . . .\n"
        "3 w . w . w . w .\n"
        "2 . w . w . w . w\n"
        "1 w . w . w . w .\n"
        "  a b c d e f g h\n";
    unsigned char start[64] = {0};
    for (int r = 0; r < 8; r++)
        for (int c = 0; c < 8; c++)
            if ((r + c) % 2 == 1)
                start[r * 8 + c] = (unsigned char)(r < 3 ? 3 : (r > 4 ? 1 : 0));
    fmt_checked(start, START_PIN, "checkers start");

    static const char EMPTY_PIN[] =
        "8 . . . . . . . .\n"
        "7 . . . . . . . .\n"
        "6 . . . . . . . .\n"
        "5 . . . . . . . .\n"
        "4 . . . . . . . .\n"
        "3 . . . . . . . .\n"
        "2 . . . . . . . .\n"
        "1 . . . . . . . .\n"
        "  a b c d e f g h\n";
    unsigned char empty[64] = {0};
    fmt_checked(empty, EMPTY_PIN, "empty board");

    /* cells[0] renders at square a8, cells[63] at h1 — pinned corners. */
    unsigned char corners[64] = {0};
    corners[0] = 2;  /* a8 = W */
    corners[7] = 4;  /* h8 = B */
    corners[56] = 1; /* a1 = w */
    corners[63] = 3; /* h1 = b */
    char out[FRAME + 1];
    long n = board_fmt(corners, out);
    CHECK(n == FRAME, "corners: returned %ld", n);
    CHECK(out[2] == 'W', "a8 glyph: got '%c' want 'W'", out[2]);
    CHECK(out[16] == 'B', "h8 glyph: got '%c' want 'B'", out[16]);
    CHECK(out[7 * 18 + 2] == 'w', "a1 glyph: got '%c' want 'w'",
          out[7 * 18 + 2]);
    CHECK(out[7 * 18 + 16] == 'b', "h1 glyph: got '%c' want 'b'",
          out[7 * 18 + 16]);
    fmt_checked(corners, NULL, "corners");
}

static unsigned int lcg = 8181u;
static unsigned int rnd(void) {
    lcg = (lcg * 1103515245u + 12345u) & 0x7fffffffu;
    return lcg >> 16;
}

static void test_sweep(void) {
    unsigned char cells[64];

    /* every glyph value, saturated boards */
    for (int v = 0; v <= 4; v++) {
        memset(cells, v, sizeof cells);
        fmt_checked(cells, NULL, "saturated board");
    }
    /* value gradient hitting all rows/columns */
    for (int i = 0; i < 64; i++)
        cells[i] = (unsigned char)(i % 5);
    fmt_checked(cells, NULL, "gradient board");

    /* random boards */
    for (int t = 0; t < 200; t++) {
        for (int i = 0; i < 64; i++)
            cells[i] = (unsigned char)(rnd() % 5);
        fmt_checked(cells, NULL, "random board");
    }
}

int main(void) {
    test_pinned_positions();
    test_sweep();
    if (failures) {
        printf("%d of %d checks failed\n", failures, checks);
        return 1;
    }
    printf("all %d checks passed\n", checks);
    return 0;
}
