/* Differential harness for the assembly Life kernel.
 *
 * life_step is linked from lifestep.s. Every result is compared against the
 * C reference below; the assembly is never trusted on its own word. Canary
 * bytes around the output buffer catch out-of-bounds stores, and the input
 * grid is snapshotted to prove the kernel treats it as read-only.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void life_step(long width, long height, const unsigned char *in,
               unsigned char *out);

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

/* Reference: one generation, B3/S23, toroidal wrap. Each of the eight
 * offsets contributes, even when wrap makes offsets coincide on tiny
 * grids — that is the pinned law. */
static void ref_step(long w, long h, const unsigned char *in,
                     unsigned char *out) {
    for (long y = 0; y < h; y++) {
        for (long x = 0; x < w; x++) {
            int n = 0;
            for (long dy = -1; dy <= 1; dy++) {
                for (long dx = -1; dx <= 1; dx++) {
                    if (dy == 0 && dx == 0)
                        continue;
                    long ny = (y + dy + h) % h;
                    long nx = (x + dx + w) % w;
                    n += in[ny * w + nx];
                }
            }
            unsigned char alive = in[y * w + x];
            out[y * w + x] = (n == 3 || (alive && n == 2)) ? 1 : 0;
        }
    }
}

#define GUARD 16
#define CANARY 0x5A

/* Run the asm kernel with guarded output and a snapshotted input, compare
 * against the reference, and leave the asm result in result_out. */
static void step_checked(long w, long h, const unsigned char *in,
                         unsigned char *result_out, const char *what) {
    long n = w * h;
    unsigned char *want = malloc((size_t)n);
    unsigned char *snap = malloc((size_t)n);
    unsigned char *buf = malloc((size_t)n + 2 * GUARD);
    if (!want || !snap || !buf) {
        printf("FAIL: harness out of memory\n");
        exit(1);
    }
    memcpy(snap, in, (size_t)n);
    memset(buf, CANARY, (size_t)n + 2 * GUARD);

    ref_step(w, h, in, want);
    life_step(w, h, in, buf + GUARD);

    CHECK(memcmp(buf + GUARD, want, (size_t)n) == 0,
          "%s: %ldx%ld grid does not match reference", what, w, h);
    int lo_ok = 1, hi_ok = 1;
    for (int i = 0; i < GUARD; i++) {
        lo_ok &= (buf[i] == CANARY);
        hi_ok &= (buf[GUARD + n + i] == CANARY);
    }
    CHECK(lo_ok, "%s: stored before the output buffer", what);
    CHECK(hi_ok, "%s: stored past the end of the output buffer", what);
    CHECK(memcmp(snap, in, (size_t)n) == 0,
          "%s: input grid was modified", what);

    if (result_out)
        memcpy(result_out, buf + GUARD, (size_t)n);
    free(want);
    free(snap);
    free(buf);
}

/* --- pinned pattern fixtures ------------------------------------------- */

static void test_blinker(void) {
    /* Vertical blinker centered on a 5x5 torus flips to horizontal and
     * back — exact frames pinned. */
    unsigned char v[25] = {0}, hz[25] = {0}, out[25];
    v[1 * 5 + 2] = v[2 * 5 + 2] = v[3 * 5 + 2] = 1;
    hz[2 * 5 + 1] = hz[2 * 5 + 2] = hz[2 * 5 + 3] = 1;

    step_checked(5, 5, v, out, "blinker gen1");
    CHECK(memcmp(out, hz, 25) == 0, "blinker did not turn horizontal");
    step_checked(5, 5, out, out, "blinker gen2");
    CHECK(memcmp(out, v, 25) == 0, "blinker did not return to vertical");
}

static void test_block(void) {
    /* 2x2 block still life on 4x4 is fixed. */
    unsigned char b[16] = {0}, out[16];
    b[1 * 4 + 1] = b[1 * 4 + 2] = b[2 * 4 + 1] = b[2 * 4 + 2] = 1;
    step_checked(4, 4, b, out, "block");
    CHECK(memcmp(out, b, 16) == 0, "block still life changed");
}

static void test_glider_torus(void) {
    /* A glider on an 8x8 torus translates by (+1,+1) every 4 generations,
     * including across the wrap. Run 32 generations = 8 laps back to the
     * start pattern, checking the shift each lap. */
    unsigned char g[64] = {0}, cur[64], want[64];
    g[0 * 8 + 1] = 1;
    g[1 * 8 + 2] = 1;
    g[2 * 8 + 0] = g[2 * 8 + 1] = g[2 * 8 + 2] = 1;
    memcpy(cur, g, 64);
    for (int lap = 1; lap <= 8; lap++) {
        for (int s = 0; s < 4; s++)
            step_checked(8, 8, cur, cur, "glider");
        memset(want, 0, 64);
        for (int y = 0; y < 8; y++)
            for (int x = 0; x < 8; x++)
                if (g[y * 8 + x])
                    want[((y + lap) % 8) * 8 + ((x + lap) % 8)] = 1;
        CHECK(memcmp(cur, want, 64) == 0,
              "glider off-position after %d generations", 4 * lap);
    }
}

static void test_tiny_grids(void) {
    /* Degenerate tori: wrap makes several of the eight offsets land on the
     * same cell, and every offset still counts. */
    unsigned char one_live = 1, one_dead = 0, out1;
    step_checked(1, 1, &one_live, &out1, "1x1 live");
    CHECK(out1 == 0, "lone 1x1 live cell must die (8 neighbors = itself)");
    step_checked(1, 1, &one_dead, &out1, "1x1 dead");
    CHECK(out1 == 0, "1x1 dead cell must stay dead");

    unsigned char full22[4] = {1, 1, 1, 1}, out4[4];
    unsigned char none[4] = {0, 0, 0, 0};
    step_checked(2, 2, full22, out4, "2x2 full");
    CHECK(memcmp(out4, none, 4) == 0,
          "full 2x2 torus must die out (every cell counts 8 live)");
}

/* --- fixture sweep ------------------------------------------------------ */

static unsigned int lcg = 20260716u;
static unsigned int rnd(void) {
    lcg = (lcg * 1103515245u + 12345u) & 0x7fffffffu;
    return lcg >> 16;
}

static void test_sweep(void) {
    static const long sizes[][2] = {
        {1, 1}, {1, 8}, {8, 1}, {2, 2}, {3, 3}, {2, 7},  {5, 4},
        {7, 7}, {9, 5}, {16, 16}, {13, 11}, {32, 9}, {24, 24},
    };
    unsigned char in[1024], out[1024];
    for (size_t s = 0; s < sizeof sizes / sizeof sizes[0]; s++) {
        long w = sizes[s][0], h = sizes[s][1], n = w * h;
        for (int trial = 0; trial < 8; trial++) {
            unsigned int density = 2 + (unsigned)trial;
            for (long i = 0; i < n; i++)
                in[i] = (rnd() % 10 < density) ? 1 : 0;
            step_checked(w, h, in, out, "sweep");
        }
    }
    /* Iterated evolution: feed the kernel its own output for 24 steps and
     * stay locked to the reference the whole way. */
    long w = 16, h = 16;
    for (long i = 0; i < w * h; i++)
        in[i] = (rnd() % 10 < 4) ? 1 : 0;
    for (int s = 0; s < 24; s++)
        step_checked(w, h, in, in, "iterated");
}

int main(void) {
    test_blinker();
    test_block();
    test_glider_torus();
    test_tiny_grids();
    test_sweep();
    if (failures) {
        printf("%d of %d checks failed\n", failures, checks);
        return 1;
    }
    printf("all %d checks passed\n", checks);
    return 0;
}
