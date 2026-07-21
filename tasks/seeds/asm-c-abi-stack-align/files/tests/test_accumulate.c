#include "accumulate.h"

#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

extern uint64_t abi_call_count;
extern uint64_t abi_bad_alignment;
extern uint64_t abi_trace_hash;

uint64_t call_with_nonvolatile_sentinels(const long *samples, size_t count,
                                         long bias, long *result);

static int failures;

static void expect_u64(const char *case_name, const char *field,
                       uint64_t actual, uint64_t expected) {
    if (actual == expected)
        return;
    fprintf(stderr, "%s: %s: got 0x%016" PRIx64 ", want 0x%016" PRIx64 "\n",
            case_name, field, actual, expected);
    failures++;
}

static long reference_sum(const long *samples, size_t count, long bias) {
    long total = 0;
    size_t index;
    for (index = 0; index < count; index++)
        total += samples[index] * 3 + (long)index * 5 - bias;
    return total;
}

static uint64_t rotate_left_17(uint64_t value) {
    return (value << 17) | (value >> (64 - 17));
}

static uint64_t reference_trace(const long *samples, size_t count, long bias) {
    uint64_t trace = 0;
    size_t index;
    for (index = 0; index < count; index++) {
        uint64_t event = (uint64_t)samples[index] ^ ((uint64_t)index << 32) ^
                         rotate_left_17((uint64_t)bias);
        trace = trace * UINT64_C(257) + event;
    }
    return trace;
}

static void run_case(const char *name, const long *samples, size_t count,
                     long bias) {
    long result = 0x1234;
    uint64_t damaged;

    abi_call_count = 0;
    abi_bad_alignment = 0;
    abi_trace_hash = 0;
    damaged = call_with_nonvolatile_sentinels(samples, count, bias, &result);

    expect_u64(name, "return value", (uint64_t)result,
               (uint64_t)reference_sum(samples, count, bias));
    expect_u64(name, "callback count", abi_call_count, (uint64_t)count);
    expect_u64(name, "callback argument/order trace", abi_trace_hash,
               reference_trace(samples, count, bias));
    expect_u64(name, "misaligned callback entries", abi_bad_alignment, 0);
    expect_u64(name, "callee-saved corruption mask", damaged, 0);
}

int main(void) {
    static const long singleton[] = {17};
    static const long mixed[] = {-11, 0, 7, 21, -4};
    static const long sequence[] = {3, 1, 4, 1, 5, 9, 2, 6};

    run_case("empty", NULL, 0, 99);
    run_case("singleton", singleton, 1, -8);
    run_case("mixed", mixed, sizeof mixed / sizeof mixed[0], 6);
    run_case("sequence", sequence, sizeof sequence / sizeof sequence[0], 13);

    if (failures != 0) {
        fprintf(stderr, "ABI acceptance failed with %d mismatch(es)\n", failures);
        return 1;
    }
    puts("ABI runtime checks passed");
    return 0;
}
