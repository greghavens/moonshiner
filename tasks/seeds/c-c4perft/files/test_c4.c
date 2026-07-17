/* Acceptance suite for the Connect Four bitboard kernel.
 * Node counts were computed with an independent implementation; any
 * disagreement means drop/legal/win is wrong somewhere. */
#include "mintest.h"
#include "c4.h"

TEST(init_and_basic_drops) {
    c4 p;
    c4_init(&p);
    CHECK_EQ_INT((long long)p.red, 0, "red board starts empty");
    CHECK_EQ_INT((long long)p.yel, 0, "yellow board starts empty");
    CHECK_EQ_INT(p.moves, 0, "move counter starts at zero");
    CHECK_EQ_INT(c4_legal(&p), 0x7F, "all seven columns open at start");
    CHECK_EQ_INT(c4_drop(&p, 3), 0, "red drops in the center");
    CHECK_EQ_INT((long long)p.red, (long long)(1ULL << 21),
                 "red stone lands at col 3 row 0 (bit 21)");
    CHECK_EQ_INT(c4_drop(&p, 3), 0, "yellow stacks on top");
    CHECK_EQ_INT((long long)p.yel, (long long)(1ULL << 22),
                 "yellow stone lands at col 3 row 1 (bit 22)");
    CHECK_EQ_INT(p.moves, 2, "two stones played");
    CHECK_EQ_INT(c4_drop(&p, -1), -1, "column below range rejected");
    CHECK_EQ_INT(c4_drop(&p, 7), -1, "column above range rejected");
}

TEST(column_fills_up) {
    c4 p;
    CHECK_EQ_INT(c4_encode(&p, "000000"), 0, "six alternating stones fill col 0");
    CHECK_EQ_INT((long long)p.red, 0x15, "red owns rows 0,2,4 of col 0");
    CHECK_EQ_INT((long long)p.yel, 0x2A, "yellow owns rows 1,3,5 of col 0");
    CHECK_EQ_INT(c4_legal(&p), 0x7E, "full column drops out of the mask");
    CHECK_EQ_INT(c4_drop(&p, 0), -1, "seventh stone in a column rejected");
    CHECK_EQ_INT(p.moves, 6, "rejected drop does not count");
}

TEST(shift_win_detector) {
    CHECK_EQ_INT(c4_win(0), 0, "empty board has no win");
    CHECK_EQ_INT(c4_win(0x204081ULL), 1, "horizontal four on the bottom row");
    CHECK_EQ_INT(c4_win(0x3C000ULL), 1, "vertical four in column 2");
    CHECK_EQ_INT(c4_win(0x1010101ULL), 1, "rising diagonal four");
    CHECK_EQ_INT(c4_win(0x208208ULL), 1, "falling diagonal four");
    CHECK_EQ_INT(c4_win(0x10004081ULL), 0, "three plus a gap is not a win");
    CHECK_EQ_INT(c4_win(0x204081ULL << 7), 1,
                 "horizontal four shifted a column still wins");
    CHECK_EQ_INT(c4_win(0x7ULL), 0, "vertical three is not a win");
}

TEST(encode_validation) {
    c4 p;
    CHECK_EQ_INT(c4_encode(&p, NULL), -1, "NULL move string rejected");
    CHECK_EQ_INT(c4_encode(&p, ""), 0, "empty string is the start position");
    CHECK_EQ_INT(c4_legal(&p), 0x7F, "empty replay leaves all columns open");
    CHECK_EQ_INT(c4_encode(&p, "337"), -1, "digit 7 rejected");
    CHECK_EQ_INT(c4_encode(&p, "3x4"), -1, "non-digit rejected");
    CHECK_EQ_INT(c4_encode(&p, "0000000"), -1, "overfilling a column rejected");
    CHECK_EQ_INT(c4_encode(&p, "34343434"), -1, "move after the game ends rejected");
}

TEST(finished_game_is_terminal) {
    c4 p;
    CHECK_EQ_INT(c4_encode(&p, "3434343"), 0, "red stacks four in column 3");
    CHECK_EQ_INT(c4_win(p.red), 1, "red has the vertical four");
    CHECK_EQ_INT(c4_win(p.yel), 0, "yellow has nothing");
    CHECK_EQ_INT(c4_legal(&p), 0, "no legal moves after a win");
    CHECK_EQ_INT(c4_drop(&p, 0), -1, "drops after a win are rejected");
    CHECK_EQ_INT(c4_perft(&p, 1), 0, "a finished game has no children");
    CHECK_EQ_INT(c4_perft(&p, 4), 0, "at any depth");
    CHECK_EQ_INT(c4_perft(&p, 0), 1, "but is itself one node at depth 0");
}

TEST(perft_start_position) {
    c4 p;
    static const long want[] = {7, 49, 343, 2401, 16807, 117649};
    int d;
    c4_init(&p);
    CHECK_EQ_INT(c4_perft(&p, 0), 1, "depth 0 is one node");
    CHECK_EQ_INT(c4_perft(&p, -1), 0, "negative depth is zero nodes");
    for (d = 1; d <= 6; d++)
        CHECK_EQ_INT(c4_perft(&p, d), want[d - 1],
                     "start-position node count is exact");
}

TEST(perft_full_column) {
    c4 p;
    static const long want[] = {6, 36, 216, 1296, 7776, 45936};
    int d;
    CHECK_EQ_INT(c4_encode(&p, "000000"), 0, "replay the full-column fixture");
    for (d = 1; d <= 6; d++)
        CHECK_EQ_INT(c4_perft(&p, d), want[d - 1],
                     "full-column node count is exact");
}

TEST(perft_double_threat) {
    c4 p;
    static const long want[] = {7, 35, 245, 1295, 8712, 48242};
    int d;
    CHECK_EQ_INT(c4_encode(&p, "334455"), 0, "replay the double-threat fixture");
    CHECK_EQ_INT((long long)p.red, 0x810200000LL,
                 "red bitboard after the replay");
    CHECK_EQ_INT((long long)p.yel, 0x1020400000LL,
                 "yellow bitboard after the replay");
    for (d = 1; d <= 6; d++)
        CHECK_EQ_INT(c4_perft(&p, d), want[d - 1],
                     "double-threat node count is exact");
}

TEST(perft_midgame) {
    c4 p;
    static const long want[] = {7, 49, 328, 2165, 13357, 85429};
    int d;
    CHECK_EQ_INT(c4_encode(&p, "33443535"), 0, "replay the midgame fixture");
    for (d = 1; d <= 6; d++)
        CHECK_EQ_INT(c4_perft(&p, d), want[d - 1],
                     "midgame node count is exact");
}

int main(void) {
    RUN(init_and_basic_drops);
    RUN(column_fills_up);
    RUN(shift_win_detector);
    RUN(encode_validation);
    RUN(finished_game_is_terminal);
    RUN(perft_start_position);
    RUN(perft_full_column);
    RUN(perft_double_threat);
    RUN(perft_midgame);
    return mt_summary();
}
