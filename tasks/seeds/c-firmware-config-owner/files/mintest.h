/* mintest.h — minimal single-header test harness (C17, no dependencies). */
#ifndef MINTEST_H
#define MINTEST_H

#include <stdio.h>
#include <string.h>

static int mt_checks;
static int mt_failed;

#define TEST(name) static void test_##name(void)
#define RUN(name) test_##name()

#define CHECK(cond, msg) do {                                               \
        mt_checks++;                                                        \
        if (!(cond)) {                                                      \
            mt_failed++;                                                    \
            fprintf(stderr, "FAIL %s:%d: %s [%s]\n",                        \
                    __FILE__, __LINE__, (msg), #cond);                      \
        }                                                                   \
    } while (0)

#define CHECK_EQ_INT(got, want, msg) do {                                   \
        long long mt_g = (long long)(got), mt_w = (long long)(want);        \
        mt_checks++;                                                        \
        if (mt_g != mt_w) {                                                 \
            mt_failed++;                                                    \
            fprintf(stderr, "FAIL %s:%d: %s (got %lld, want %lld)\n",       \
                    __FILE__, __LINE__, (msg), mt_g, mt_w);                 \
        }                                                                   \
    } while (0)

#define CHECK_EQ_STR(got, want, msg) do {                                   \
        const char *mt_gs = (got), *mt_ws = (want);                         \
        mt_checks++;                                                        \
        if (mt_gs == NULL || strcmp(mt_gs, mt_ws) != 0) {                   \
            mt_failed++;                                                    \
            fprintf(stderr, "FAIL %s:%d: %s (got \"%s\", want \"%s\")\n",   \
                    __FILE__, __LINE__, (msg),                              \
                    mt_gs ? mt_gs : "(null)", mt_ws);                       \
        }                                                                   \
    } while (0)

static int mt_summary(void) {
    if (mt_failed) {
        fprintf(stderr, "FAILED: %d of %d checks\n", mt_failed, mt_checks);
        return 1;
    }
    printf("ok: %d checks passed\n", mt_checks);
    return 0;
}

#endif /* MINTEST_H */
