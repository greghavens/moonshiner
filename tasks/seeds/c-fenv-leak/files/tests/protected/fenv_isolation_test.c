#include "rounding_scope.h"

#include <errno.h>
#include <fenv.h>
#include <inttypes.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum case_result {
    CASE_PASS = 0,
    CASE_FAIL = 1
};

struct test_case {
    const char *name;
    int (*run)(void);
};

static const char *rounding_name(int mode)
{
    if (mode == FE_TONEAREST) {
        return "FE_TONEAREST";
    }
    if (mode == FE_DOWNWARD) {
        return "FE_DOWNWARD";
    }
    if (mode == FE_UPWARD) {
        return "FE_UPWARD";
    }
    if (mode == FE_TOWARDZERO) {
        return "FE_TOWARDZERO";
    }
    return "unknown";
}

static int nearest_tie_case(void)
{
    volatile double input = 1.5;
    double actual = nearbyint(input);
    int mode = fegetround();

    if (mode != FE_TONEAREST || actual != 2.0) {
        fprintf(stderr,
                "nearest-tie: expected nearbyint(1.5) = 2 under "
                "FE_TONEAREST, got %.17g under %s\n",
                actual,
                rounding_name(mode));
        return CASE_FAIL;
    }
    return CASE_PASS;
}

static int successful_probe(void *context)
{
    volatile double input = 1.25;
    double *observed = context;

    *observed = nearbyint(input);
    if (fegetround() != FE_UPWARD || *observed != 2.0) {
        return CASE_FAIL;
    }
    return ROUNDING_SCOPE_OK;
}

static int successful_scope_case(void)
{
    double observed = 0.0;
    int result = rounding_scope_run(FE_UPWARD, successful_probe, &observed);
    int mode = fegetround();

    if (result != ROUNDING_SCOPE_OK || mode != FE_TONEAREST) {
        fprintf(stderr,
                "successful-scope: result %d, observed %.17g, active mode %s\n",
                result,
                observed,
                rounding_name(mode));
        return CASE_FAIL;
    }
    return CASE_PASS;
}

static int rejected_lower_bound_probe(void *context)
{
    volatile double input = 1.75;
    double *lower_bound = context;

    *lower_bound = nearbyint(input);
    if (fegetround() != FE_DOWNWARD || *lower_bound != 1.0) {
        return CASE_FAIL;
    }

    /* This sample is outside the model domain; rejection is expected. */
    return ROUNDING_SCOPE_REJECTED;
}

static int rejecting_lower_bound_case(void)
{
    double lower_bound = 0.0;
    int result = rounding_scope_run(FE_DOWNWARD,
                                    rejected_lower_bound_probe,
                                    &lower_bound);

    if (result != ROUNDING_SCOPE_REJECTED || lower_bound != 1.0) {
        fprintf(stderr,
                "rejecting-lower-bound: expected rejection with bound 1, "
                "got result %d and %.17g\n",
                result,
                lower_bound);
        return CASE_FAIL;
    }
    return CASE_PASS;
}

static uint64_t next_shuffle_value(uint64_t *state)
{
    *state = (*state * UINT64_C(6364136223846793005))
             + UINT64_C(1442695040888963407);
    return *state;
}

static void shuffle_cases(struct test_case *cases, size_t count, uint64_t seed)
{
    size_t index;
    uint64_t state = seed;

    for (index = count; index > 1U; --index) {
        uint64_t random_value = next_shuffle_value(&state);
        size_t swap_index = (size_t)((random_value >> 32U) % index);
        struct test_case temporary = cases[index - 1U];

        cases[index - 1U] = cases[swap_index];
        cases[swap_index] = temporary;
    }
}

static int run_case(const struct test_case *test_case)
{
    int result = test_case->run();

    if (result == CASE_PASS) {
        printf("PASS %s\n", test_case->name);
    } else {
        fprintf(stderr, "FAIL %s\n", test_case->name);
    }
    return result;
}

static int run_direct(void)
{
    const struct test_case test_case = {"nearest-tie", nearest_tie_case};

    if (fesetround(FE_TONEAREST) != 0) {
        fprintf(stderr, "could not establish FE_TONEAREST\n");
        return CASE_FAIL;
    }
    return run_case(&test_case);
}

static int parse_seed(const char *text, uint64_t *seed)
{
    char *end = NULL;
    uintmax_t parsed;

    errno = 0;
    parsed = strtoumax(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || parsed > UINT64_MAX) {
        return CASE_FAIL;
    }
    *seed = (uint64_t)parsed;
    return CASE_PASS;
}

static int run_shuffled(const char *seed_text)
{
    struct test_case cases[] = {
        {"rejecting-lower-bound", rejecting_lower_bound_case},
        {"nearest-tie", nearest_tie_case},
        {"successful-scope", successful_scope_case}
    };
    const char *expected_order[] = {
        "successful-scope",
        "rejecting-lower-bound",
        "nearest-tie"
    };
    const size_t count = sizeof(cases) / sizeof(cases[0]);
    uint64_t seed;
    size_t index;
    int failures = 0;

    if (parse_seed(seed_text, &seed) != CASE_PASS || seed != UINT64_C(100103)) {
        fprintf(stderr, "shuffled mode requires the pinned seed 100103\n");
        return CASE_FAIL;
    }
    if (fesetround(FE_TONEAREST) != 0) {
        fprintf(stderr, "could not establish FE_TONEAREST\n");
        return CASE_FAIL;
    }

    shuffle_cases(cases, count, seed);
    printf("SHUFFLE seed=%" PRIu64 " order=", seed);
    for (index = 0; index < count; ++index) {
        printf("%s%s", index == 0U ? "" : ",", cases[index].name);
        if (strcmp(cases[index].name, expected_order[index]) != 0) {
            fprintf(stderr, "\nseed 100103 no longer produces the pinned order\n");
            return CASE_FAIL;
        }
    }
    putchar('\n');

    for (index = 0; index < count; ++index) {
        failures += run_case(&cases[index]);
    }
    return failures == 0 ? CASE_PASS : CASE_FAIL;
}

static int restoration_probe(void *context)
{
    int *expected_result = context;

    if (fegetround() != FE_DOWNWARD) {
        return CASE_FAIL;
    }
    return *expected_result;
}

static int check_restoration_path(const char *name, int callback_result)
{
    int supplied_result = callback_result;
    int result;
    int observed_mode;
    int failed = CASE_PASS;

    if (fesetround(FE_UPWARD) != 0) {
        fprintf(stderr, "%s: could not establish FE_UPWARD\n", name);
        return CASE_FAIL;
    }

    result = rounding_scope_run(FE_DOWNWARD,
                                restoration_probe,
                                &supplied_result);
    observed_mode = fegetround();
    if (result != callback_result || observed_mode != FE_UPWARD) {
        fprintf(stderr,
                "%s: expected result %d and restored FE_UPWARD, got %d and %s\n",
                name,
                callback_result,
                result,
                rounding_name(observed_mode));
        failed = CASE_FAIL;
    }

    if (fesetround(FE_TONEAREST) != 0) {
        fprintf(stderr, "%s: could not restore harness mode\n", name);
        failed = CASE_FAIL;
    }
    if (failed == CASE_PASS) {
        printf("PASS %s\n", name);
    } else {
        fprintf(stderr, "FAIL %s\n", name);
    }
    return failed;
}

static int run_restoration(void)
{
    int failures = 0;

    failures += check_restoration_path("restore-success-path",
                                       ROUNDING_SCOPE_OK);
    failures += check_restoration_path("restore-rejection-path",
                                       ROUNDING_SCOPE_REJECTED);
    return failures == 0 ? CASE_PASS : CASE_FAIL;
}

static void usage(const char *program)
{
    fprintf(stderr,
            "usage: %s direct | shuffled 100103 | restoration\n",
            program);
}

int main(int argc, char **argv)
{
    (void)setvbuf(stdout, NULL, _IONBF, 0);
    (void)setvbuf(stderr, NULL, _IONBF, 0);

    if (argc == 2 && strcmp(argv[1], "direct") == 0) {
        return run_direct();
    }
    if (argc == 3 && strcmp(argv[1], "shuffled") == 0) {
        return run_shuffled(argv[2]);
    }
    if (argc == 2 && strcmp(argv[1], "restoration") == 0) {
        return run_restoration();
    }

    usage(argv[0]);
    return 2;
}
