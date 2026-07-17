/* Acceptance suite for the cribbage show scorer.
 * Every breakdown below was cross-checked against an independent scorer;
 * the pinned numbers are the contract. */
#include "mintest.h"
#include "cribbage.h"

/* Pin all six fields of one scored hand in one shot. */
#define CHECK_SCORE(hand, starter, crib, f15, prs, rns, fls, nbs, tot) do {  \
        CribBreakdown bd = crib_score((hand), (starter), (crib));            \
        CHECK_EQ_INT(bd.fifteens, (f15), hand " / " starter ": fifteens");   \
        CHECK_EQ_INT(bd.pairs,    (prs), hand " / " starter ": pairs");      \
        CHECK_EQ_INT(bd.runs,     (rns), hand " / " starter ": runs");       \
        CHECK_EQ_INT(bd.flush,    (fls), hand " / " starter ": flush");      \
        CHECK_EQ_INT(bd.nobs,     (nbs), hand " / " starter ": nobs");       \
        CHECK_EQ_INT(bd.total,    (tot), hand " / " starter ": total");      \
    } while (0)

TEST(the_29_hand) {
    /* Three fives and his nobs jack, cut the matching five. */
    CHECK_SCORE("5C 5D 5S JH", "5H", false, 16, 12, 0, 0, 1, 29);
}

TEST(the_28_hand_starter_jack_scores_no_nobs) {
    /* Nobs is a jack IN HAND matching the starter suit; a jack turned as
     * the starter is worth nothing here (that point belongs to the deal). */
    CHECK_SCORE("5C 5D 5H 5S", "JH", false, 16, 12, 0, 0, 0, 28);
}

TEST(fifteens_count_every_distinct_combination) {
    CHECK_SCORE("TC JD QH KS", "5C", false, 8, 0, 4, 0, 0, 12);
    CHECK_SCORE("9C 6D 3H 6S", "6H", false, 12, 6, 0, 0, 0, 18);
    CHECK_SCORE("QC KD AH 7S", "8C", false, 2, 0, 0, 0, 0, 2);
}

TEST(pairs_by_multiplicity) {
    CHECK_SCORE("4C 4D 4H 4S", "5C", false, 0, 12, 0, 0, 0, 12);
    CHECK_SCORE("7C 8D 9H JD", "JH", false, 2, 2, 3, 0, 0, 7);
}

TEST(single_runs) {
    CHECK_SCORE("AC 2D 3H KS", "QC", false, 4, 0, 3, 0, 0, 7);
    CHECK_SCORE("5C 6D 7H 8S", "9C", false, 4, 0, 5, 0, 0, 9);
}

TEST(ace_is_low_only) {
    /* A-2-3 runs; Q-K-A does not wrap. */
    CHECK_SCORE("QC KD AH 7S", "8C", false, 2, 0, 0, 0, 0, 2);
    CHECK_SCORE("AC AD 2H 3S", "4C", false, 0, 2, 8, 0, 0, 10);
}

TEST(double_run) {
    /* 4-5-6-6: the pair doubles the run of three. */
    CHECK_SCORE("4C 5D 6H 6S", "9C", false, 8, 2, 6, 0, 0, 16);
}

TEST(triple_run) {
    /* 7-7-7-8-9: three sevens make three runs of three. */
    CHECK_SCORE("7C 7D 7H 8S", "9C", false, 6, 6, 9, 0, 0, 21);
}

TEST(double_double_run) {
    /* 8-8-9-9-T: four runs of three, and only the maximal run counts —
     * no extra credit for sub-runs inside it. */
    CHECK_SCORE("8C 8D 9H 9S", "TC", false, 0, 4, 12, 0, 0, 16);
    CHECK_SCORE("6C 7D 8H 8S", "7H", false, 8, 4, 12, 0, 0, 24);
}

TEST(hand_flush_is_four_or_five) {
    CHECK_SCORE("2H 4H 6H 8H", "KC", false, 0, 0, 0, 4, 0, 4);
    CHECK_SCORE("2H 4H 6H 8H", "KH", false, 0, 0, 0, 5, 0, 5);
}

TEST(crib_flush_needs_all_five) {
    /* The same four hearts: worth 4 in hand, worth 0 in the crib unless
     * the starter is a heart too. */
    CHECK_SCORE("2H 4H 6H 8H", "KC", true, 0, 0, 0, 0, 0, 0);
    CHECK_SCORE("2H 4H 6H 8H", "KH", true, 0, 0, 0, 5, 0, 5);
}

TEST(his_nobs) {
    CHECK_SCORE("JC 3D 7H KS", "2C", false, 4, 0, 0, 0, 1, 5);
    /* Jack of the wrong suit is just a ten-card. */
    CHECK_SCORE("7C 8D 9H JD", "JH", false, 2, 2, 3, 0, 0, 7);
}

TEST(the_zero_hand) {
    CHECK_SCORE("KC QD 8H 2S", "AC", false, 0, 0, 0, 0, 0, 0);
}

int main(void) {
    RUN(the_29_hand);
    RUN(the_28_hand_starter_jack_scores_no_nobs);
    RUN(fifteens_count_every_distinct_combination);
    RUN(pairs_by_multiplicity);
    RUN(single_runs);
    RUN(ace_is_low_only);
    RUN(double_run);
    RUN(triple_run);
    RUN(double_double_run);
    RUN(hand_flush_is_four_or_five);
    RUN(crib_flush_needs_all_five);
    RUN(his_nobs);
    RUN(the_zero_hand);
    return mt_summary();
}
