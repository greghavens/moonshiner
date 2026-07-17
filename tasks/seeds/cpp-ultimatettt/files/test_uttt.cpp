/* Acceptance suite for the ultimate tic-tac-toe engine.
 * The three long transcripts were generated and end-state-checked with an
 * independent implementation of the stated rules; every pin is exact. */
#include "mintest.h"
#include "uttt.h"

/* X wins sub-boards 0, 4 and 8 for the macro diagonal; no other board
 * closes along the way. */
static const char *X_TAKES_THE_DIAGONAL =
    "4:7 7:7 7:2 2:7 7:3 3:1 1:7 7:5 5:8 8:8 8:1 1:0 0:0 0:7 7:4 4:6 "
    "6:7 7:8 8:0 0:3 3:4 4:8 8:2 2:8 0:8 3:0 0:2 2:0 0:5 5:4 4:1 1:5 "
    "5:1 1:6 6:6 6:0 4:0 5:5 5:2 2:2 2:6 6:2 2:5 5:6 6:5 5:0 4:4";

/* Every board closes (X takes 1/3/8, O takes 5/6, four boards fill drawn)
 * and no macro line ever forms. */
static const char *ALL_BOARDS_CLOSE_DRAWN =
    "3:7 7:0 0:2 2:8 8:1 1:8 8:8 8:3 3:0 0:6 6:4 4:0 0:0 0:4 4:4 4:2 "
    "2:1 1:6 6:6 6:3 3:4 4:8 8:7 7:5 5:0 0:1 1:0 0:3 3:6 6:1 1:1 1:2 "
    "2:6 6:7 7:8 8:0 0:5 5:6 6:5 5:2 2:5 5:4 4:1 1:3 3:3 7:7 7:6 6:0 "
    "0:7 7:2 2:0 0:8 8:4 4:3 1:5 2:4 4:5 2:7 7:3 2:3 6:8 2:2 1:7 7:4 "
    "4:6 6:2 1:4 4:7 7:1";

/* X owns boards 0 and 1, board 2 fills without a line — the whole top
 * macro row is closed yet nobody has won anything. */
static const char *TOP_ROW_BUT_NO_LINE =
    "8:4 4:0 0:6 6:1 1:2 2:2 2:4 4:3 3:5 5:2 2:8 8:7 7:6 6:8 8:2 2:0 "
    "0:2 2:3 3:4 4:7 7:8 8:6 6:3 3:2 2:5 5:0 0:3 3:0 0:7 7:4 4:1 1:6 "
    "6:6 6:4 4:4 4:8 8:3 3:7 7:2 2:7 7:1 1:4 4:6 6:2 2:1 1:3 3:8 8:5 "
    "5:5 5:8 8:0 0:4 4:5 5:6 6:7 7:5 5:7 7:0 0:0 7:7 7:3 3:3 3:1 1:7 "
    "1:0 8:1 1:1 4:2 2:6";

TEST(opening_position) {
    UTTT g;
    ut_init(&g);
    CHECK_EQ_INT(ut_to_move(&g), 1, "X moves first");
    CHECK_EQ_INT(ut_forced(&g), -1, "first move is a free choice");
    CHECK_EQ_INT(ut_winner(&g), 0, "no winner at the start");
    CHECK_EQ_INT(ut_cell(&g, 4, 4), 0, "center cell empty");
    CHECK_EQ_INT(ut_cell(&g, 9, 0), -1, "board index out of range reads -1");
    CHECK_EQ_INT(ut_cell(&g, 0, 9), -1, "cell index out of range reads -1");
    CHECK_EQ_INT(ut_subwinner(&g, 4), 0, "every sub-board starts open");
}

TEST(the_sent_board_rule) {
    UTTT g;
    ut_init(&g);
    CHECK(ut_play(&g, 4, 7), "X opens anywhere");
    CHECK_EQ_INT(ut_forced(&g), 7, "cell 7 sends O to board 7");
    CHECK_EQ_INT(ut_to_move(&g), 2, "O to move");
    CHECK(!ut_play(&g, 3, 0), "playing outside the sent board is illegal");
    CHECK_EQ_INT(ut_to_move(&g), 2, "an illegal try does not burn the turn");
    CHECK(ut_play(&g, 7, 4), "O obeys the sent board");
    CHECK_EQ_INT(ut_forced(&g), 4, "and sends X back to the center board");
}

TEST(illegal_moves_and_replay_index) {
    UTTT g;
    ut_init(&g);
    CHECK(!ut_play(&g, 9, 0), "board out of range rejected");
    CHECK(!ut_play(&g, 0, 9), "cell out of range rejected");
    CHECK(!ut_play(&g, -1, 4), "negative board rejected");
    CHECK_EQ_INT(ut_replay(&g, "4:4 4:4 0:0"), 1,
                 "occupied cell flagged at index 1");
    CHECK_EQ_INT(ut_cell(&g, 4, 4), 1, "the legal prefix stays on the board");
    CHECK_EQ_INT(ut_to_move(&g), 2, "turn stops with the prefix");
    CHECK_EQ_INT(ut_replay(&g, "4:0 1:1"), 1,
                 "sent-board violation flagged at index 1");
    CHECK_EQ_INT(ut_replay(&g, "4:7 7:0 0:4"), -1, "clean replay returns -1");
    CHECK_EQ_INT(ut_forced(&g), 4, "replay leaves the forced board set");
}

TEST(winning_a_board_closes_and_frees_it) {
    UTTT g;
    ut_init(&g);
    CHECK_EQ_INT(ut_replay(&g, "0:1 1:0 0:2 2:0 0:0"), -1,
                 "X collects the top row of board 0");
    CHECK_EQ_INT(ut_subwinner(&g, 0), 1, "board 0 belongs to X");
    CHECK_EQ_INT(ut_winner(&g), 0, "one board is not the match");
    CHECK_EQ_INT(ut_forced(&g), -1,
                 "sent to a won board means free choice");
    CHECK(!ut_play(&g, 0, 3), "a won board takes no more marks");
    CHECK(ut_play(&g, 5, 5), "O plays anywhere instead");
    CHECK_EQ_INT(ut_forced(&g), 5, "normal sending resumes");
    CHECK(ut_play(&g, 5, 0), "X follows to board 5");
    CHECK_EQ_INT(ut_forced(&g), -1,
                 "cell 0 points at the closed board, so free choice again");
}

TEST(macro_diagonal_win) {
    UTTT g;
    ut_init(&g);
    CHECK_EQ_INT(ut_replay(&g, X_TAKES_THE_DIAGONAL), -1,
                 "the full game replays cleanly");
    CHECK_EQ_INT(ut_winner(&g), 1, "X wins the match on the diagonal");
    CHECK_EQ_INT(ut_subwinner(&g, 0), 1, "board 0 is X's");
    CHECK_EQ_INT(ut_subwinner(&g, 4), 1, "board 4 is X's");
    CHECK_EQ_INT(ut_subwinner(&g, 8), 1, "board 8 is X's");
    CHECK_EQ_INT(ut_subwinner(&g, 5), 0, "board 5 never closed");
    CHECK(!ut_play(&g, 5, 3), "no moves after the match is decided");
}

TEST(drawn_boards_count_for_neither) {
    UTTT g;
    ut_init(&g);
    CHECK_EQ_INT(ut_replay(&g, TOP_ROW_BUT_NO_LINE), -1,
                 "the transcript replays cleanly");
    CHECK_EQ_INT(ut_subwinner(&g, 0), 1, "board 0 is X's");
    CHECK_EQ_INT(ut_subwinner(&g, 1), 1, "board 1 is X's");
    CHECK_EQ_INT(ut_subwinner(&g, 2), 3, "board 2 filled with no line");
    CHECK_EQ_INT(ut_subwinner(&g, 4), 3, "board 4 filled with no line");
    CHECK_EQ_INT(ut_subwinner(&g, 7), 3, "board 7 filled with no line");
    CHECK_EQ_INT(ut_winner(&g), 0,
                 "X-X-drawn across the top is not a macro line");
    CHECK_EQ_INT(ut_to_move(&g), 2, "O is on the clock");
    CHECK_EQ_INT(ut_forced(&g), 6, "and was sent to board 6");
    CHECK(ut_play(&g, 6, 0), "play simply continues");
}

TEST(macro_draw) {
    UTTT g;
    ut_init(&g);
    CHECK_EQ_INT(ut_replay(&g, ALL_BOARDS_CLOSE_DRAWN), -1,
                 "the full game replays cleanly");
    CHECK_EQ_INT(ut_winner(&g), 3, "all boards closed, no macro line: draw");
    CHECK_EQ_INT(ut_subwinner(&g, 0), 3, "board 0 drawn");
    CHECK_EQ_INT(ut_subwinner(&g, 1), 1, "board 1 X");
    CHECK_EQ_INT(ut_subwinner(&g, 2), 3, "board 2 drawn");
    CHECK_EQ_INT(ut_subwinner(&g, 3), 1, "board 3 X");
    CHECK_EQ_INT(ut_subwinner(&g, 4), 3, "board 4 drawn");
    CHECK_EQ_INT(ut_subwinner(&g, 5), 2, "board 5 O");
    CHECK_EQ_INT(ut_subwinner(&g, 6), 2, "board 6 O");
    CHECK_EQ_INT(ut_subwinner(&g, 7), 3, "board 7 drawn");
    CHECK_EQ_INT(ut_subwinner(&g, 8), 1, "board 8 X");
    CHECK(!ut_play(&g, 0, 0), "a drawn match takes no more moves");
}

int main(void) {
    RUN(opening_position);
    RUN(the_sent_board_rule);
    RUN(illegal_moves_and_replay_index);
    RUN(winning_a_board_closes_and_frees_it);
    RUN(macro_diagonal_win);
    RUN(drawn_boards_count_for_neither);
    RUN(macro_draw);
    return mt_summary();
}
