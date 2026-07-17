/* Acceptance tests for the block-allocation bitmap (bitvec.h).
 * Build and run with `make test`.
 *
 * Contract highlights: caller-owned word storage, half-open ranges with
 * strict bounds (reject, never clamp), and bits past nbits in the last
 * word must never turn on.
 */
#include "mintest.h"

#include <stdint.h>

#include "bitvec.h"

#define WORDS 4

TEST(init_validates_and_zeroes_dirty_storage) {
    uint64_t words[WORDS] = {~0ull, ~0ull, ~0ull, ~0ull};
    bitvec bv;
    CHECK_EQ_INT(bv_init(&bv, words, 130), 0, "init succeeds");
    CHECK_EQ_INT(bv_count(&bv), 0, "freshly initialized map is empty");
    CHECK_EQ_INT(bv_test(&bv, 0), 0, "bit 0 starts clear");
    CHECK_EQ_INT(bv_test(&bv, 129), 0, "last bit starts clear");
    CHECK_EQ_INT(bv_init(&bv, NULL, 130), -1, "NULL storage rejected");
    CHECK_EQ_INT(bv_init(&bv, words, 0), -1, "zero-bit map rejected");
}

TEST(single_bit_set_test_and_bounds) {
    uint64_t words[WORDS];
    bitvec bv;
    bv_init(&bv, words, 130);
    CHECK_EQ_INT(bv_set(&bv, 0), 0, "set bit 0");
    CHECK_EQ_INT(bv_set(&bv, 63), 0, "set last bit of word 0");
    CHECK_EQ_INT(bv_set(&bv, 64), 0, "set first bit of word 1");
    CHECK_EQ_INT(bv_set(&bv, 129), 0, "set the final bit");
    CHECK_EQ_INT(bv_count(&bv), 4, "four bits set");
    CHECK_EQ_INT(bv_test(&bv, 0), 1, "bit 0 reads back");
    CHECK_EQ_INT(bv_test(&bv, 63), 1, "bit 63 reads back");
    CHECK_EQ_INT(bv_test(&bv, 64), 1, "bit 64 reads back");
    CHECK_EQ_INT(bv_test(&bv, 129), 1, "bit 129 reads back");
    CHECK_EQ_INT(bv_test(&bv, 1), 0, "untouched bit stays clear");
    CHECK_EQ_INT(bv_set(&bv, 130), -1, "set past the end is rejected");
    CHECK_EQ_INT(bv_test(&bv, 130), -1, "test past the end is rejected");
    CHECK_EQ_INT(bv_flip(&bv, 999), -1, "flip far past the end rejected");
    CHECK_EQ_INT(bv_count(&bv), 4, "rejected calls change nothing");
}

TEST(clear_and_flip_roundtrip) {
    uint64_t words[WORDS];
    bitvec bv;
    bv_init(&bv, words, 128);
    bv_set(&bv, 5);
    CHECK_EQ_INT(bv_clear(&bv, 5), 0, "clear succeeds");
    CHECK_EQ_INT(bv_test(&bv, 5), 0, "cleared bit reads clear");
    CHECK_EQ_INT(bv_flip(&bv, 7), 0, "flip up");
    CHECK_EQ_INT(bv_test(&bv, 7), 1, "flipped bit is set");
    CHECK_EQ_INT(bv_flip(&bv, 7), 0, "flip down");
    CHECK_EQ_INT(bv_test(&bv, 7), 0, "double flip is identity");
    bv_flip(&bv, 63);
    CHECK_EQ_INT(bv_test(&bv, 63), 1, "flip works at a word edge");
    CHECK_EQ_INT(bv_count(&bv), 1, "one bit left set");
}

TEST(range_within_one_word) {
    uint64_t words[WORDS];
    bitvec bv;
    bv_init(&bv, words, 64);
    CHECK_EQ_INT(bv_set_range(&bv, 3, 11), 0, "set [3,11)");
    CHECK_EQ_INT(bv_count(&bv), 8, "eight bits set");
    CHECK_EQ_INT(bv_test(&bv, 2), 0, "bit below the range clear");
    CHECK_EQ_INT(bv_test(&bv, 3), 1, "range start set");
    CHECK_EQ_INT(bv_test(&bv, 10), 1, "last bit inside set");
    CHECK_EQ_INT(bv_test(&bv, 11), 0, "half-open end stays clear");
}

TEST(range_at_exact_word_boundaries) {
    uint64_t words[WORDS];
    bitvec bv;
    bv_init(&bv, words, 192);
    CHECK_EQ_INT(bv_set_range(&bv, 0, 64), 0, "whole first word");
    CHECK_EQ_INT(bv_count(&bv), 64, "64 bits after first word");
    CHECK_EQ_INT(bv_set_range(&bv, 64, 128), 0, "whole second word");
    CHECK_EQ_INT(bv_count(&bv), 128, "128 bits after second word");
    CHECK_EQ_INT(bv_test(&bv, 128), 0, "third word untouched");
    CHECK_EQ_INT(bv_clear_range(&bv, 0, 192), 0, "clear everything");
    CHECK_EQ_INT(bv_count(&bv), 0, "empty again");
}

TEST(range_straddles_word_seams) {
    uint64_t words[WORDS];
    bitvec bv;
    bv_init(&bv, words, 192);
    CHECK_EQ_INT(bv_set_range(&bv, 60, 70), 0, "set across the 64 seam");
    CHECK_EQ_INT(bv_count(&bv), 10, "ten bits set");
    CHECK_EQ_INT(bv_test(&bv, 59), 0, "below range");
    CHECK_EQ_INT(bv_test(&bv, 60), 1, "start");
    CHECK_EQ_INT(bv_test(&bv, 63), 1, "last bit of word 0");
    CHECK_EQ_INT(bv_test(&bv, 64), 1, "first bit of word 1");
    CHECK_EQ_INT(bv_test(&bv, 69), 1, "last bit inside");
    CHECK_EQ_INT(bv_test(&bv, 70), 0, "end stays clear");
    bv_set_range(&bv, 120, 130);
    CHECK_EQ_INT(bv_count(&bv), 20, "second straddle adds ten");
    CHECK_EQ_INT(bv_clear_range(&bv, 63, 66), 0, "clear across the seam");
    CHECK_EQ_INT(bv_count(&bv), 17, "three bits cleared");
    CHECK_EQ_INT(bv_test(&bv, 62), 1, "62 survives");
    CHECK_EQ_INT(bv_test(&bv, 63), 0, "63 cleared");
    CHECK_EQ_INT(bv_test(&bv, 65), 0, "65 cleared");
    CHECK_EQ_INT(bv_test(&bv, 66), 1, "66 survives");
}

TEST(empty_and_bad_ranges) {
    uint64_t words[WORDS];
    bitvec bv;
    bv_init(&bv, words, 192);
    bv_set_range(&bv, 10, 20);
    CHECK_EQ_INT(bv_set_range(&bv, 5, 5), 0, "empty range is a no-op");
    CHECK_EQ_INT(bv_count(&bv), 10, "no-op changed nothing");
    CHECK_EQ_INT(bv_set_range(&bv, 10, 300), -1, "hi past nbits rejected");
    CHECK_EQ_INT(bv_count(&bv), 10, "rejected set_range changed nothing");
    CHECK_EQ_INT(bv_set_range(&bv, 20, 10), -1, "lo > hi rejected");
    CHECK_EQ_INT(bv_clear_range(&bv, 0, 193), -1, "clear_range past end");
    CHECK_EQ_INT(bv_count(&bv), 10, "rejected clear_range changed nothing");
    CHECK_EQ_INT(bv_count_range(&bv, 10, 5), -1, "count_range lo > hi");
    CHECK_EQ_INT(bv_count_range(&bv, 0, 193), -1, "count_range past end");
}

TEST(count_range_hits_partial_words) {
    uint64_t words[WORDS];
    bitvec bv;
    bv_init(&bv, words, 192);
    bv_set(&bv, 5);
    bv_set(&bv, 64);
    bv_set(&bv, 100);
    bv_set(&bv, 130);
    bv_set(&bv, 191);
    CHECK_EQ_INT(bv_count_range(&bv, 0, 192), 5, "whole map");
    CHECK_EQ_INT(bv_count_range(&bv, 5, 6), 1, "single-bit window");
    CHECK_EQ_INT(bv_count_range(&bv, 6, 64), 0, "gap window");
    CHECK_EQ_INT(bv_count_range(&bv, 64, 65), 1, "window at the seam");
    CHECK_EQ_INT(bv_count_range(&bv, 0, 131), 4, "prefix window");
    CHECK_EQ_INT(bv_count_range(&bv, 101, 191), 1, "interior window");
    CHECK_EQ_INT(bv_count_range(&bv, 191, 192), 1, "final bit window");
    CHECK_EQ_INT(bv_count_range(&bv, 0, 0), 0, "empty window");
}

TEST(next_set_walks_the_map) {
    uint64_t words[WORDS];
    bitvec bv;
    bv_init(&bv, words, 192);
    bv_set(&bv, 5);
    bv_set(&bv, 64);
    bv_set(&bv, 100);
    bv_set(&bv, 130);
    bv_set(&bv, 191);
    CHECK_EQ_INT(bv_next_set(&bv, 0), 5, "first hop");
    CHECK_EQ_INT(bv_next_set(&bv, 5), 5, "from lands on a set bit");
    CHECK_EQ_INT(bv_next_set(&bv, 6), 64, "hop across a zero word");
    CHECK_EQ_INT(bv_next_set(&bv, 65), 100, "hop within a word");
    CHECK_EQ_INT(bv_next_set(&bv, 101), 130, "next seam hop");
    CHECK_EQ_INT(bv_next_set(&bv, 131), 191, "hop to the last bit");
    CHECK_EQ_INT(bv_next_set(&bv, 191), 191, "from == last set bit");
    CHECK_EQ_INT(bv_next_set(&bv, 192), -1, "from past the end");
    bv_clear(&bv, 191);
    CHECK_EQ_INT(bv_next_set(&bv, 131), -1, "nothing left to find");
}

TEST(next_clear_in_a_dense_map) {
    uint64_t words[WORDS];
    bitvec bv;
    bv_init(&bv, words, 130);
    bv_set_range(&bv, 0, 130);
    CHECK_EQ_INT(bv_next_clear(&bv, 0), -1, "full map has no free bit");
    bv_clear(&bv, 64);
    CHECK_EQ_INT(bv_next_clear(&bv, 0), 64, "finds the hole");
    CHECK_EQ_INT(bv_next_clear(&bv, 64), 64, "from on the hole");
    CHECK_EQ_INT(bv_next_clear(&bv, 65), -1, "no hole after 64");
    bv_clear(&bv, 129);
    CHECK_EQ_INT(bv_next_clear(&bv, 65), 129, "finds the last-bit hole");
}

TEST(tail_word_discipline) {
    uint64_t words[2];
    bitvec bv;
    bv_init(&bv, words, 70);
    CHECK_EQ_INT(bv_set_range(&bv, 0, 70), 0, "fill the whole map");
    CHECK_EQ_INT(bv_count(&bv), 70, "exactly 70 bits counted");
    CHECK_EQ_INT(bv_clear_range(&bv, 64, 70), 0, "clear the tail");
    CHECK_EQ_INT(bv_count(&bv), 64, "64 bits remain");
    CHECK_EQ_INT(bv_next_set(&bv, 64), -1, "tail is really clear");
    bv_flip(&bv, 69);
    CHECK_EQ_INT(bv_count(&bv), 65, "flip in the tail counts once");
    CHECK_EQ_INT(bv_next_set(&bv, 64), 69, "tail bit is findable");
}

TEST(every_third_block) {
    uint64_t words[WORDS];
    bitvec bv;
    bv_init(&bv, words, 256);
    for (size_t i = 0; i < 256; i += 3)
        bv_set(&bv, i);
    CHECK_EQ_INT(bv_count(&bv), 86, "86 blocks marked");
    CHECK_EQ_INT(bv_count_range(&bv, 0, 3), 1, "one in the first stripe");
    CHECK_EQ_INT(bv_count_range(&bv, 3, 9), 2, "two in [3,9)");
    CHECK_EQ_INT(bv_count_range(&bv, 250, 256), 2, "two in the last stripe");
    CHECK_EQ_INT(bv_next_set(&bv, 1), 3, "skip to the next stripe");
    CHECK_EQ_INT(bv_next_set(&bv, 4), 6, "and the next");
    CHECK_EQ_INT(bv_next_clear(&bv, 3), 4, "first free block after 3");
}

int main(void) {
    RUN(init_validates_and_zeroes_dirty_storage);
    RUN(single_bit_set_test_and_bounds);
    RUN(clear_and_flip_roundtrip);
    RUN(range_within_one_word);
    RUN(range_at_exact_word_boundaries);
    RUN(range_straddles_word_seams);
    RUN(empty_and_bad_ranges);
    RUN(count_range_hits_partial_words);
    RUN(next_set_walks_the_map);
    RUN(next_clear_in_a_dense_map);
    RUN(tail_word_discipline);
    RUN(every_third_block);
    return mt_summary();
}
