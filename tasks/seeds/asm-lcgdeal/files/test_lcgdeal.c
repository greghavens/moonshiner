/* Differential harness for the assembly LCG + deck-shuffle helpers.
 *
 * lcg_next, lcg_rand and lcg_deal are linked from lcgdeal.s. The pinned
 * literals below were computed with an independent oracle of the stated
 * generator, so the C reference and the assembly are checked against the
 * spec, not just against each other.
 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>

uint32_t lcg_next(uint32_t *state);
uint32_t lcg_rand(uint32_t *state, uint32_t bound);
void lcg_deal(uint32_t seed, unsigned char *deck);

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

/* --- C reference of the pinned generator -------------------------------- */

static uint32_t ref_next(uint32_t *s) {
    *s = (*s * 1103515245u + 12345u) & 0x7fffffffu;
    return *s;
}

static uint32_t ref_rand(uint32_t *s, uint32_t bound) {
    ref_next(s);
    return (*s >> 16) % bound;
}

static void ref_deal(uint32_t seed, unsigned char *deck) {
    uint32_t s = seed & 0x7fffffffu;
    for (int i = 0; i < 52; i++)
        deck[i] = (unsigned char)i;
    for (int i = 51; i >= 1; i--) {
        uint32_t j = ref_rand(&s, (uint32_t)i + 1u);
        unsigned char t = deck[i];
        deck[i] = deck[j];
        deck[j] = t;
    }
}

/* --- pinned literals (independent oracle) ------------------------------- */

static void test_pinned_states(void) {
    static const struct {
        uint32_t seed;
        uint32_t states[8];
    } pins[] = {
        {1, {1103527590u, 377401575u, 662824084u, 1147902781u,
             2035015474u, 368800899u, 1508029952u, 486256185u}},
        {42, {1250496027u, 1116302264u, 1000676753u, 1668674806u,
              908095735u, 71666532u, 896336333u, 1736731266u}},
        {2026, {191421147u, 783686008u, 169580625u, 365869238u,
                1477462455u, 1877955876u, 313550989u, 1594206786u}},
    };
    for (size_t p = 0; p < sizeof pins / sizeof pins[0]; p++) {
        uint32_t s = pins[p].seed;
        for (int k = 0; k < 8; k++) {
            uint32_t r = lcg_next(&s);
            CHECK(r == pins[p].states[k],
                  "lcg_next seed %u draw %d: got %u want %u",
                  pins[p].seed, k, r, pins[p].states[k]);
            CHECK(s == r,
                  "lcg_next seed %u draw %d: *state %u != returned %u",
                  pins[p].seed, k, s, r);
        }
    }
}

static void test_pinned_rand(void) {
    static const uint32_t bounds[5] = {52, 6, 100, 2, 13};
    static const uint32_t want[10] = {12, 2, 68, 0, 5, 37, 0, 90, 1, 6};
    uint32_t s = 7;
    for (int k = 0; k < 10; k++) {
        uint32_t r = lcg_rand(&s, bounds[k % 5]);
        CHECK(r == want[k], "lcg_rand seed 7 draw %d: got %u want %u",
              k, r, want[k]);
    }
}

static void test_pinned_decks(void) {
    static const struct {
        uint32_t seed;
        unsigned char deck[52];
    } pins[] = {
        {1, {6, 25, 0, 15, 41, 28, 12, 7, 26, 30, 17, 32, 44, 38, 49, 2,
             21, 35, 29, 14, 50, 23, 31, 45, 47, 37, 3, 27, 40, 36, 18, 48,
             24, 11, 8, 33, 5, 51, 9, 4, 16, 19, 1, 20, 39, 10, 34, 43,
             22, 13, 46, 42}},
        {42, {4, 42, 24, 2, 46, 44, 3, 38, 0, 31, 26, 17, 29, 43, 27, 47,
              16, 41, 5, 23, 45, 21, 11, 7, 20, 28, 34, 10, 13, 39, 48, 25,
              35, 51, 18, 9, 6, 37, 36, 14, 22, 8, 33, 1, 40, 15, 12, 32,
              30, 19, 50, 49}},
        {2026, {40, 31, 13, 12, 18, 15, 39, 21, 27, 35, 16, 33, 22, 41, 34,
                4, 11, 20, 26, 51, 43, 48, 3, 46, 42, 19, 7, 29, 6, 9, 10,
                17, 23, 30, 14, 2, 1, 38, 44, 50, 49, 36, 28, 5, 25, 0, 47,
                32, 45, 37, 24, 8}},
    };
    unsigned char deck[52];
    for (size_t p = 0; p < sizeof pins / sizeof pins[0]; p++) {
        memset(deck, 0xEE, sizeof deck);
        lcg_deal(pins[p].seed, deck);
        CHECK(memcmp(deck, pins[p].deck, 52) == 0,
              "lcg_deal seed %u: deck does not match pinned oracle",
              pins[p].seed);
    }
}

/* --- differential sweeps ------------------------------------------------ */

static void test_next_sweep(void) {
    for (uint32_t seed = 0; seed < 200; seed++) {
        uint32_t sa = seed, sr = seed;
        for (int k = 0; k < 100; k++) {
            uint32_t a = lcg_next(&sa);
            uint32_t r = ref_next(&sr);
            if (a != r || sa != sr) {
                CHECK(0, "lcg_next diverges: seed %u draw %d (%u vs %u)",
                      seed, k, a, r);
                return;
            }
        }
        CHECK(sa == sr, "lcg_next state sweep seed %u", seed);
    }
    /* States above 2^31 feed through the same update law. */
    uint32_t sa = 0xffffffffu, sr = 0xffffffffu;
    for (int k = 0; k < 20; k++) {
        uint32_t a = lcg_next(&sa), r = ref_next(&sr);
        CHECK(a == r && a <= 0x7fffffffu,
              "lcg_next with high-bit state: draw %d got %u want %u",
              k, a, r);
    }
}

static void test_rand_sweep(void) {
    static const uint32_t bounds[] = {1, 2, 3, 6, 13, 52, 53, 100, 32768};
    for (size_t b = 0; b < sizeof bounds / sizeof bounds[0]; b++) {
        uint32_t sa = 90000u + (uint32_t)b, sr = sa;
        for (int k = 0; k < 200; k++) {
            uint32_t a = lcg_rand(&sa, bounds[b]);
            uint32_t r = ref_rand(&sr, bounds[b]);
            if (a != r || sa != sr) {
                CHECK(0, "lcg_rand bound %u draw %d: got %u want %u",
                      bounds[b], k, a, r);
                return;
            }
            if (a >= bounds[b]) {
                CHECK(0, "lcg_rand bound %u returned %u", bounds[b], a);
                return;
            }
        }
        CHECK(sa == sr, "lcg_rand state sweep bound %u", bounds[b]);
    }
}

static void test_deal_sweep(void) {
    unsigned char a[52], r[52];
    int seen[52];
    for (uint32_t seed = 0; seed < 500; seed++) {
        lcg_deal(seed, a);
        ref_deal(seed, r);
        if (memcmp(a, r, 52) != 0) {
            CHECK(0, "lcg_deal seed %u: deck does not match reference",
                  seed);
            return;
        }
        memset(seen, 0, sizeof seen);
        int perm = 1;
        for (int i = 0; i < 52; i++) {
            if (a[i] > 51 || seen[a[i]])
                perm = 0;
            else
                seen[a[i]] = 1;
        }
        CHECK(perm, "lcg_deal seed %u: deck is not a permutation of 0..51",
              seed);
    }
    checks++; /* whole-sweep marker */

    /* Seed bit 31 is masked before the first draw. */
    unsigned char m[52];
    lcg_deal(0x80000001u, m);
    lcg_deal(1u, a);
    CHECK(memcmp(m, a, 52) == 0,
          "lcg_deal must mask the seed to 31 bits (0x80000001 == 1)");

    /* Guard bytes just past the deck stay untouched. */
    unsigned char buf[60];
    memset(buf, 0x5A, sizeof buf);
    lcg_deal(7u, buf);
    int ok = 1;
    for (int i = 52; i < 60; i++)
        ok &= (buf[i] == 0x5A);
    CHECK(ok, "lcg_deal wrote past the 52-byte deck");
}

int main(void) {
    test_pinned_states();
    test_pinned_rand();
    test_pinned_decks();
    test_next_sweep();
    test_rand_sweep();
    test_deal_sweep();
    if (failures) {
        printf("%d of %d checks failed\n", failures, checks);
        return 1;
    }
    printf("all %d checks passed\n", checks);
    return 0;
}
