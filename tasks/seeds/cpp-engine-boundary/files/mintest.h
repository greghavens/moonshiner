#ifndef MINTEST_H
#define MINTEST_H

#include <cmath>
#include <cstdio>

static int mt_checks;
static int mt_failed;

#define TEST(name) static void test_##name()
#define RUN(name) test_##name()

#define CHECK(cond, msg) do {                                               \
    ++mt_checks;                                                            \
    if (!(cond)) {                                                          \
        ++mt_failed;                                                        \
        std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, msg); \
    }                                                                       \
} while (0)

#define CHECK_EQ(got, want, msg) do {                                       \
    const long long mt_g = static_cast<long long>(got);                     \
    const long long mt_w = static_cast<long long>(want);                    \
    ++mt_checks;                                                            \
    if (mt_g != mt_w) {                                                     \
        ++mt_failed;                                                        \
        std::fprintf(stderr, "FAIL %s:%d: %s (got %lld, want %lld)\n",     \
                     __FILE__, __LINE__, msg, mt_g, mt_w);                  \
    }                                                                       \
} while (0)

#define CHECK_NEAR(got, want, eps, msg) do {                                \
    const double mt_g = static_cast<double>(got);                           \
    const double mt_w = static_cast<double>(want);                          \
    ++mt_checks;                                                            \
    if (std::fabs(mt_g - mt_w) > static_cast<double>(eps)) {                \
        ++mt_failed;                                                        \
        std::fprintf(stderr, "FAIL %s:%d: %s (got %.12f, want %.12f)\n",   \
                     __FILE__, __LINE__, msg, mt_g, mt_w);                  \
    }                                                                       \
} while (0)

static int mt_summary() {
    if (mt_failed != 0) {
        std::fprintf(stderr, "FAILED: %d of %d checks\n", mt_failed, mt_checks);
        return 1;
    }
    std::printf("ok: %d checks passed\n", mt_checks);
    return 0;
}

#endif
