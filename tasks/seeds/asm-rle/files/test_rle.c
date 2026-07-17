/* Differential harness for the assembly run-length codec.
 *
 * rle_encode and rle_decode are linked from rle.s and compared against the
 * C reference below on fixtures, capacity sweeps around the exact-fit
 * boundary, and run-biased random buffers. Canary bytes directly after the
 * declared capacity catch any store past cap — on success AND on failure.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

long rle_encode(const unsigned char *src, long n, unsigned char *dst,
                long cap);
long rle_decode(const unsigned char *src, long n, unsigned char *dst,
                long cap);

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

/* --- C reference of the pinned codec ------------------------------------ */

static long ref_encode(const unsigned char *src, long n, unsigned char *dst,
                       long cap) {
    long r = 0, w = 0;
    while (r < n) {
        unsigned char v = src[r];
        long run = 1;
        while (r + run < n && src[r + run] == v && run < 255)
            run++;
        if (w + 2 > cap)
            return -1;
        dst[w++] = (unsigned char)run;
        dst[w++] = v;
        r += run;
    }
    return w;
}

static long ref_decode(const unsigned char *src, long n, unsigned char *dst,
                       long cap) {
    if (n % 2 != 0)
        return -1;
    long w = 0;
    for (long r = 0; r < n; r += 2) {
        long count = src[r];
        if (count == 0)
            return -1;
        if (w + count > cap)
            return -1;
        memset(dst + w, src[r + 1], (size_t)count);
        w += count;
    }
    return w;
}

/* --- guarded differential calls ------------------------------------------
 * Buffer layout: [cap payload bytes][GUARD canary bytes]. The asm result
 * must match the reference return value; when both succeed the payloads
 * must match byte for byte; the canaries must survive either way. */

#define GUARD 16
#define CANARY 0xA5
#define BUFMAX 4096

static void enc_both(const unsigned char *src, long n, long cap,
                     const char *what) {
    static unsigned char got[BUFMAX + GUARD], want[BUFMAX + GUARD];
    memset(got, CANARY, sizeof got);
    memset(want, CANARY, sizeof want);
    long rw = ref_encode(src, n, want, cap);
    long rg = rle_encode(src, n, got, cap);
    CHECK(rg == rw, "%s: encode(n=%ld,cap=%ld) returned %ld want %ld",
          what, n, cap, rg, rw);
    if (rw >= 0 && rg == rw)
        CHECK(memcmp(got, want, (size_t)rw) == 0,
              "%s: encode(n=%ld,cap=%ld) payload mismatch", what, n, cap);
    int ok = 1;
    for (long i = cap; i < cap + GUARD; i++)
        ok &= (got[i] == CANARY);
    CHECK(ok, "%s: encode(n=%ld,cap=%ld) stored past cap", what, n, cap);
}

static void dec_both(const unsigned char *src, long n, long cap,
                     const char *what) {
    static unsigned char got[BUFMAX + GUARD], want[BUFMAX + GUARD];
    memset(got, CANARY, sizeof got);
    memset(want, CANARY, sizeof want);
    long rw = ref_decode(src, n, want, cap);
    long rg = rle_decode(src, n, got, cap);
    CHECK(rg == rw, "%s: decode(n=%ld,cap=%ld) returned %ld want %ld",
          what, n, cap, rg, rw);
    if (rw >= 0 && rg == rw)
        CHECK(memcmp(got, want, (size_t)rw) == 0,
              "%s: decode(n=%ld,cap=%ld) payload mismatch", what, n, cap);
    int ok = 1;
    for (long i = cap; i < cap + GUARD; i++)
        ok &= (got[i] == CANARY);
    CHECK(ok, "%s: decode(n=%ld,cap=%ld) stored past cap", what, n, cap);
}

/* Encode with a roomy buffer, decode it back, expect the original. */
static void round_trip(const unsigned char *src, long n, const char *what) {
    static unsigned char enc[BUFMAX], back[BUFMAX];
    long e = rle_encode(src, n, enc, BUFMAX);
    CHECK(e >= 0, "%s: round-trip encode failed (%ld)", what, e);
    if (e < 0)
        return;
    long d = rle_decode(enc, e, back, BUFMAX);
    CHECK(d == n, "%s: round-trip length %ld want %ld", what, d, n);
    if (d == n)
        CHECK(memcmp(back, src, (size_t)n) == 0,
              "%s: round-trip bytes differ", what);
    /* exact-fit boundary sweep on both directions */
    for (long cap = (e > 3 ? e - 3 : 0); cap <= e + 2; cap++)
        enc_both(src, n, cap, what);
    for (long cap = (n > 3 ? n - 3 : 0); cap <= n + 2; cap++)
        dec_both(enc, e, cap, what);
}

/* --- pinned literals ----------------------------------------------------- */

static void test_pinned(void) {
    static unsigned char buf[BUFMAX];

    /* empty input encodes to zero bytes, even with cap 0 */
    enc_both(buf, 0, 0, "empty");
    CHECK(rle_encode(buf, 0, buf, 0) == 0, "encode of empty must return 0");
    CHECK(rle_decode(buf, 0, buf, 0) == 0, "decode of empty must return 0");

    /* single byte */
    unsigned char one[1] = {7};
    unsigned char got[8];
    long r = rle_encode(one, 1, got, 8);
    CHECK(r == 2 && got[0] == 1 && got[1] == 7,
          "encode({7}) must yield {1,7}, got %ld bytes", r);

    /* short run: three 'a' */
    unsigned char aaa[3] = {'a', 'a', 'a'};
    r = rle_encode(aaa, 3, got, 8);
    CHECK(r == 2 && got[0] == 3 && got[1] == 'a',
          "encode(aaa) must yield {3,'a'}, got %ld bytes", r);

    /* run splitting at 255, pinned greedy: 300 -> {255,v},{45,v} */
    static unsigned char run300[300];
    memset(run300, 9, sizeof run300);
    r = rle_encode(run300, 300, buf, 16);
    CHECK(r == 4 && buf[0] == 255 && buf[1] == 9 && buf[2] == 45 &&
              buf[3] == 9,
          "encode(300x9) must yield {255,9,45,9}, got %ld bytes", r);
    r = rle_encode(run300, 255, buf, 16);
    CHECK(r == 2 && buf[0] == 255 && buf[1] == 9,
          "encode(255x9) must yield {255,9}, got %ld bytes", r);
    r = rle_encode(run300, 256, buf, 16);
    CHECK(r == 4 && buf[0] == 255 && buf[1] == 9 && buf[2] == 1 &&
              buf[3] == 9,
          "encode(256x9) must yield {255,9,1,9}, got %ld bytes", r);

    /* worst case: no runs at all doubles the size */
    unsigned char alt[6] = {1, 2, 1, 2, 1, 2};
    r = rle_encode(alt, 6, buf, 12);
    CHECK(r == 12, "encode(121212) must need 12 bytes, got %ld", r);
    r = rle_encode(alt, 6, buf, 11);
    CHECK(r == -1, "encode(121212) into cap 11 must return -1, got %ld", r);

    /* decode rejects: odd input, zero count, overflow */
    unsigned char odd[3] = {1, 5, 1};
    CHECK(rle_decode(odd, 3, buf, 64) == -1,
          "decode of odd-length input must return -1");
    unsigned char zc[4] = {2, 8, 0, 8};
    CHECK(rle_decode(zc, 4, buf, 64) == -1,
          "decode of a zero count byte must return -1");
    unsigned char ovf[4] = {200, 3, 200, 3};
    CHECK(rle_decode(ovf, 4, buf, 399) == -1,
          "decode overflowing cap by one byte must return -1");
    CHECK(rle_decode(ovf, 4, buf, 400) == 400,
          "decode exactly filling cap must succeed");
}

/* --- fixtures and sweeps -------------------------------------------------- */

static unsigned int lcg = 77u;
static unsigned int rnd(void) {
    lcg = (lcg * 1103515245u + 12345u) & 0x7fffffffu;
    return lcg >> 16;
}

static void test_fixtures(void) {
    /* a saved Life board: sparse rows compress hard */
    static unsigned char life[192];
    memset(life, 0, sizeof life);
    for (int i = 0; i < 192; i += 17)
        life[i] = 1;
    round_trip(life, 192, "life board");

    /* checkers-style board: repeating 4-value rows */
    static unsigned char board[64];
    for (int i = 0; i < 64; i++)
        board[i] = (unsigned char)((i / 8) % 2 ? ((i % 2) ? 0 : 3)
                                               : ((i % 2) ? 1 : 0));
    round_trip(board, 64, "checkers board");

    /* random run-biased buffers, lengths hitting the split boundaries */
    static unsigned char rb[1500];
    static const long lens[] = {1, 2, 5, 63, 254, 255, 256, 510, 511, 1500};
    for (size_t k = 0; k < sizeof lens / sizeof lens[0]; k++) {
        long n = lens[k];
        long i = 0;
        while (i < n) {
            unsigned char v = (unsigned char)(rnd() % 5);
            long run = 1 + (long)(rnd() % 300);
            while (run-- > 0 && i < n)
                rb[i++] = v;
        }
        round_trip(rb, n, "random runs");
    }
}

int main(void) {
    test_pinned();
    test_fixtures();
    if (failures) {
        printf("%d of %d checks failed\n", failures, checks);
        return 1;
    }
    printf("all %d checks passed\n", checks);
    return 0;
}
