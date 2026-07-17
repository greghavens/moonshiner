/* Exhaustive differential harness for the assembly blackjack hand evaluator.
 *
 * bj_value is linked from bjval.s. The harness enumerates EVERY card
 * sequence of length 0 through 6 over ranks 1..13 (5,229,043 hands) and
 * compares each packed result against the C reference. Aggregate totals and
 * a rolling hash over the whole walk are pinned from an independent oracle,
 * so the reference itself is cross-checked too.
 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>

int bj_value(const unsigned char *cards, long n);

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

#define SOFT 0x100
#define BUST 0x200
#define NATURAL 0x400

static int ref_value(const unsigned char *cards, long n) {
    int hard = 0, ace = 0;
    for (long i = 0; i < n; i++) {
        int c = cards[i];
        hard += (c > 10) ? 10 : c;
        if (c == 1)
            ace = 1;
    }
    int total = hard, soft = 0;
    if (ace && hard + 10 <= 21) {
        total = hard + 10;
        soft = 1;
    }
    int v = total;
    if (soft)
        v |= SOFT;
    if (total > 21)
        v |= BUST;
    if (n == 2 && total == 21)
        v |= NATURAL;
    return v;
}

static void hand_str(const unsigned char *cards, long n, char *s) {
    static const char *names[] = {"?", "A", "2", "3", "4",  "5",  "6",
                                  "7", "8", "9", "10", "J", "Q", "K"};
    s[0] = 0;
    if (n == 0) {
        strcpy(s, "(empty)");
        return;
    }
    for (long i = 0; i < n; i++) {
        strcat(s, names[cards[i]]);
        if (i + 1 < n)
            strcat(s, " ");
    }
}

/* Pinned spot hands — literal expectations from an independent oracle. */
static void test_spots(void) {
    static const struct {
        unsigned char cards[6];
        long n;
        int want;
    } spots[] = {
        {{1, 13}, 2, 0x515},          /* A K: soft 21, natural */
        {{1, 10}, 2, 0x515},          /* A 10: also a natural */
        {{1, 5, 5}, 3, 0x115},        /* A 5 5: soft 21, NOT natural */
        {{1, 1}, 2, 0x10c},           /* A A: soft 12 */
        {{2, 1}, 2, 0x10d},           /* 2 A: soft 13 */
        {{10, 11}, 2, 0x14},          /* 10 J: hard 20 */
        {{7, 7, 7}, 3, 0x15},         /* 7 7 7: hard 21, not natural */
        {{13, 12, 5}, 3, 0x219},      /* K Q 5: 25, bust */
        {{1, 1, 1, 1, 1, 1}, 6, 0x110}, /* six aces: soft 16 */
        {{0}, 0, 0x0},                /* empty hand: 0, no flags */
        {{1, 6, 5}, 3, 0xc},          /* A 6 5: hard 12, ace forced to 1 */
        {{11, 12, 13}, 3, 0x21e},     /* J Q K: 30, bust */
    };
    char hs[32];
    for (size_t i = 0; i < sizeof spots / sizeof spots[0]; i++) {
        int got = bj_value(spots[i].cards, spots[i].n);
        hand_str(spots[i].cards, spots[i].n, hs);
        CHECK(got == spots[i].want, "hand %s: got 0x%x want 0x%x",
              hs, got, spots[i].want);
    }
}

/* Every hand of length 0..6, odometer order (last card cycles fastest). */
static void test_exhaustive(void) {
    unsigned char cards[6];
    char hs[32];
    long hands = 0, softs = 0, busts = 0, nats = 0;
    uint32_t roll = 0;

    for (int n = 0; n <= 6; n++) {
        for (int i = 0; i < n; i++)
            cards[i] = 1;
        for (;;) {
            int want = ref_value(cards, n);
            int got = bj_value(cards, n);
            if (got != want) {
                hand_str(cards, n, hs);
                CHECK(0, "hand %s: got 0x%x want 0x%x", hs, got, want);
                return;
            }
            hands++;
            roll = roll * 31u + (uint32_t)want;
            if (want & SOFT)
                softs++;
            if (want & BUST)
                busts++;
            if (want & NATURAL)
                nats++;
            int i = n - 1;
            while (i >= 0 && cards[i] == 13)
                cards[i--] = 1;
            if (i < 0)
                break;
            cards[i]++;
        }
    }
    checks++; /* whole-walk marker */

    /* Pinned aggregates over all 5,229,043 hands (independent oracle). */
    CHECK(hands == 5229043, "hands walked: got %ld want 5229043", hands);
    CHECK(softs == 1348, "soft hands: got %ld want 1348", softs);
    CHECK(busts == 5138061, "busted hands: got %ld want 5138061", busts);
    CHECK(nats == 8, "naturals: got %ld want 8", nats);
    CHECK(roll == 2585300619u, "rolling hash: got %u want 2585300619",
          roll);
}

int main(void) {
    test_spots();
    test_exhaustive();
    if (failures) {
        printf("%d of %d checks failed\n", failures, checks);
        return 1;
    }
    printf("all %d checks passed\n", checks);
    return 0;
}
