/* Exhaustive differential harness for the assembly tic-tac-toe evaluator.
 *
 * ttt_status and ttt_moves are linked from tttwin.s. The harness walks all
 * 3^9 = 19683 boards and compares both routines against the C reference on
 * every one. Category totals over the full walk are pinned from an
 * independent oracle, so the reference itself is cross-checked.
 */
#include <stdio.h>
#include <string.h>

int ttt_status(const unsigned char *board);
long ttt_moves(const unsigned char *board, unsigned char *out);

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

static const int LINES[8][3] = {
    {0, 1, 2}, {3, 4, 5}, {6, 7, 8},
    {0, 3, 6}, {1, 4, 7}, {2, 5, 8},
    {0, 4, 8}, {2, 4, 6},
};

static int ref_status(const unsigned char *b) {
    for (int p = 1; p <= 2; p++)
        for (int l = 0; l < 8; l++)
            if (b[LINES[l][0]] == p && b[LINES[l][1]] == p &&
                b[LINES[l][2]] == p)
                return p;
    for (int i = 0; i < 9; i++)
        if (b[i] == 0)
            return 0;
    return 3;
}

static long ref_moves(const unsigned char *b, unsigned char *out) {
    if (ref_status(b) != 0)
        return 0;
    long n = 0;
    for (int i = 0; i < 9; i++)
        if (b[i] == 0)
            out[n++] = (unsigned char)i;
    return n;
}

static void board_str(const unsigned char *b, char *s) {
    for (int i = 0; i < 9; i++)
        s[i] = ".XO"[b[i]];
    s[9] = 0;
}

/* Pinned spot boards: literal expectations, independent of the reference. */
static void test_spot_boards(void) {
    static const struct {
        unsigned char b[9];
        int status;
        long nmoves;
        unsigned char moves[9];
    } spots[] = {
        /* empty board: ongoing, all nine cells open */
        {{0, 0, 0, 0, 0, 0, 0, 0, 0}, 0, 9, {0, 1, 2, 3, 4, 5, 6, 7, 8}},
        /* X top row */
        {{1, 1, 1, 2, 2, 0, 0, 0, 0}, 1, 0, {0}},
        /* O anti-diagonal */
        {{1, 1, 2, 1, 2, 0, 2, 0, 0}, 2, 0, {0}},
        /* full board, no line: draw */
        {{1, 2, 1, 1, 2, 2, 2, 1, 1}, 3, 0, {0}},
        /* both players hold a line: X reported first — pinned */
        {{1, 1, 1, 2, 2, 2, 0, 0, 0}, 1, 0, {0}},
        /* midgame, holes at 2,5,6,8 */
        {{1, 2, 0, 1, 2, 0, 0, 1, 0}, 0, 4, {2, 5, 6, 8}},
        /* X wins with the board otherwise full: win beats draw */
        {{1, 2, 2, 2, 1, 1, 2, 1, 1}, 1, 0, {0}},
    };
    unsigned char out[9];
    char bs[10];
    for (size_t i = 0; i < sizeof spots / sizeof spots[0]; i++) {
        board_str(spots[i].b, bs);
        int st = ttt_status(spots[i].b);
        CHECK(st == spots[i].status, "status(%s): got %d want %d",
              bs, st, spots[i].status);
        memset(out, 0xEE, sizeof out);
        long n = ttt_moves(spots[i].b, out);
        CHECK(n == spots[i].nmoves, "moves(%s): count %ld want %ld",
              bs, n, spots[i].nmoves);
        if (n == spots[i].nmoves && n > 0)
            CHECK(memcmp(out, spots[i].moves, (size_t)n) == 0,
                  "moves(%s): wrong cells listed", bs);
    }
}

/* Exhaustive walk: every 9-byte board over {0,1,2}. */
static void test_all_boards(void) {
    long counts[4] = {0, 0, 0, 0};
    long total_moves = 0;
    unsigned char b[9], want_mv[9], got_mv[16];
    char bs[10];

    for (long code = 0; code < 19683; code++) {
        long v = code;
        for (int i = 0; i < 9; i++) {
            b[i] = (unsigned char)(v % 3);
            v /= 3;
        }
        int want_st = ref_status(b);
        int got_st = ttt_status(b);
        if (got_st != want_st) {
            board_str(b, bs);
            CHECK(0, "status(%s): got %d want %d", bs, got_st, want_st);
            return;
        }
        long want_n = ref_moves(b, want_mv);
        memset(got_mv, 0xEE, sizeof got_mv);
        long got_n = ttt_moves(b, got_mv);
        if (got_n != want_n ||
            (want_n > 0 && memcmp(got_mv, want_mv, (size_t)want_n) != 0)) {
            board_str(b, bs);
            CHECK(0, "moves(%s): count %ld want %ld or wrong cells",
                  bs, got_n, want_n);
            return;
        }
        int clean = 1;
        for (long i = want_n; i < 16; i++)
            clean &= (got_mv[i] == 0xEE);
        if (!clean) {
            board_str(b, bs);
            CHECK(0, "moves(%s): wrote past the returned count", bs);
            return;
        }
        counts[want_st]++;
        total_moves += want_n;
    }
    checks++; /* whole-walk marker */

    /* Pinned totals over all 19683 boards (independent oracle). */
    CHECK(counts[0] == 11093, "ongoing boards: got %ld want 11093",
          counts[0]);
    CHECK(counts[1] == 4435, "X-win boards: got %ld want 4435", counts[1]);
    CHECK(counts[2] == 4123, "O-win boards: got %ld want 4123", counts[2]);
    CHECK(counts[3] == 32, "draw boards: got %ld want 32", counts[3]);
    CHECK(total_moves == 40075,
          "legal moves across ongoing boards: got %ld want 40075",
          total_moves);
}

int main(void) {
    test_spot_boards();
    test_all_boards();
    if (failures) {
        printf("%d of %d checks failed\n", failures, checks);
        return 1;
    }
    printf("all %d checks passed\n", checks);
    return 0;
}
