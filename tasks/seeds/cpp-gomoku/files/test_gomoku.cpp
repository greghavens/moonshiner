/* Acceptance suite for the gomoku engine and threat bot.
 * Bot answers and transcripts were checked against an independent
 * implementation of the stated rules; the pins are the contract. */
#include "mintest.h"
#include "gomoku.h"

static void check_bot(const char *setup, int wantR, int wantC, const char *msg) {
    Gomoku g;
    gm_init(&g);
    if (setup && setup[0])
        CHECK_EQ_INT(gm_replay(&g, setup), -1, "bot setup transcript is legal");
    int r = -1, c = -1;
    CHECK(gm_bot(&g, &r, &c), "bot proposes a move");
    CHECK_EQ_INT(r, wantR, msg);
    CHECK_EQ_INT(c, wantC, msg);
}

TEST(fresh_board) {
    Gomoku g;
    gm_init(&g);
    CHECK_EQ_INT(gm_to_move(&g), 1, "black moves first");
    CHECK_EQ_INT(gm_winner(&g), 0, "no winner at the start");
    CHECK_EQ_INT(gm_at(&g, 7, 7), 0, "center empty");
    CHECK_EQ_INT(gm_at(&g, -1, 0), -1, "row below range reads -1");
    CHECK_EQ_INT(gm_at(&g, 0, 15), -1, "col above range reads -1");
}

TEST(play_and_alternate) {
    Gomoku g;
    gm_init(&g);
    CHECK(gm_play(&g, 7, 7), "black plays center");
    CHECK_EQ_INT(gm_at(&g, 7, 7), 1, "center is black");
    CHECK_EQ_INT(gm_to_move(&g), 2, "white to move after black");
    CHECK(!gm_play(&g, 7, 7), "occupied cell rejected");
    CHECK_EQ_INT(gm_to_move(&g), 2, "rejected move does not flip the turn");
    CHECK(!gm_play(&g, 15, 0), "row out of range rejected");
    CHECK(!gm_play(&g, 3, -1), "col out of range rejected");
    CHECK(gm_play(&g, 8, 8), "white replies");
    CHECK_EQ_INT(gm_at(&g, 8, 8), 2, "reply is white");
}

TEST(replay_reports_first_bad_move) {
    Gomoku g;
    gm_init(&g);
    CHECK_EQ_INT(gm_replay(&g, "7,7 7,7 8,8"), 1, "duplicate cell flagged at index 1");
    CHECK_EQ_INT(gm_at(&g, 7, 7), 1, "legal prefix is kept on the board");
    CHECK_EQ_INT(gm_to_move(&g), 2, "turn stops with the prefix");
    CHECK_EQ_INT(gm_replay(&g, "0,0 15,3"), 1, "out-of-range move flagged");
    CHECK_EQ_INT(gm_replay(&g, "3,3 4,4"), -1, "clean replay returns -1");
    CHECK_EQ_INT(gm_at(&g, 7, 7), 0, "replay starts from a fresh board");
}

TEST(horizontal_win_black) {
    Gomoku g;
    gm_init(&g);
    CHECK_EQ_INT(gm_replay(&g, "7,4 0,0 7,5 0,2 7,6 0,4 7,7 0,6 7,8"), -1,
                 "win transcript replays cleanly");
    CHECK_EQ_INT(gm_winner(&g), 1, "black wins with five across");
    CHECK(!gm_play(&g, 12, 12), "no moves after the game is decided");
}

TEST(vertical_win_white) {
    Gomoku g;
    gm_init(&g);
    CHECK_EQ_INT(gm_replay(&g, "0,0 5,5 0,2 6,5 0,4 7,5 0,6 8,5 0,8 9,5"), -1,
                 "white column transcript replays cleanly");
    CHECK_EQ_INT(gm_winner(&g), 2, "white wins with five down");
}

TEST(diagonal_wins) {
    Gomoku g;
    gm_init(&g);
    CHECK_EQ_INT(gm_replay(&g, "5,5 0,0 6,6 0,2 7,7 0,4 8,8 0,6 9,9"), -1,
                 "diagonal transcript replays cleanly");
    CHECK_EQ_INT(gm_winner(&g), 1, "black wins on the falling diagonal");
    gm_init(&g);
    CHECK_EQ_INT(gm_replay(&g, "5,9 0,0 6,8 0,2 7,7 0,4 8,6 0,6 9,5"), -1,
                 "anti-diagonal transcript replays cleanly");
    CHECK_EQ_INT(gm_winner(&g), 1, "black wins on the rising diagonal");
}

TEST(overline_is_not_a_win) {
    Gomoku g;
    gm_init(&g);
    /* Black joins 3+2 into a row of six at move index 10 — play continues —
     * then builds an exact five down column 3 and wins. */
    CHECK_EQ_INT(gm_replay(&g,
        "7,4 0,0 7,5 0,2 7,6 0,4 7,8 0,6 7,9 0,8 7,7"), -1,
        "overline transcript replays cleanly");
    CHECK_EQ_INT(gm_winner(&g), 0, "six in a row decides nothing");
    CHECK_EQ_INT(gm_to_move(&g), 2, "game simply continues");
    CHECK_EQ_INT(gm_replay(&g,
        "7,4 0,0 7,5 0,2 7,6 0,4 7,8 0,6 7,9 0,8 7,7 0,10 "
        "3,3 0,12 4,3 0,14 5,3 12,0 6,3 12,2 2,3"), -1,
        "full transcript replays cleanly");
    CHECK_EQ_INT(gm_winner(&g), 1, "the exact five in the column wins");
}

TEST(full_board_draw) {
    Gomoku g;
    gm_init(&g);
    CHECK_EQ_INT(gm_replay(&g,
        "0,0 0,2 0,1 0,3 0,4 0,6 0,5 0,7 0,8 0,10 0,9 0,11 0,12 0,14 0,13 "
        "1,0 1,2 1,1 1,3 1,4 1,6 1,5 1,7 1,8 1,10 1,9 1,11 1,12 1,14 1,13 "
        "2,0 2,2 2,1 2,3 2,4 2,6 2,5 2,7 2,8 2,10 2,9 2,11 2,12 2,14 2,13 "
        "3,0 3,2 3,1 3,3 3,4 3,6 3,5 3,7 3,8 3,10 3,9 3,11 3,12 3,14 3,13 "
        "4,0 4,2 4,1 4,3 4,4 4,6 4,5 4,7 4,8 4,10 4,9 4,11 4,12 4,14 4,13 "
        "5,0 5,2 5,1 5,3 5,4 5,6 5,5 5,7 5,8 5,10 5,9 5,11 5,12 5,14 5,13 "
        "6,0 6,2 6,1 6,3 6,4 6,6 6,5 6,7 6,8 6,10 6,9 6,11 6,12 6,14 6,13 "
        "7,0 7,2 7,1 7,3 7,4 7,6 7,5 7,7 7,8 7,10 7,9 7,11 7,12 7,14 7,13 "
        "8,0 8,2 8,1 8,3 8,4 8,6 8,5 8,7 8,8 8,10 8,9 8,11 8,12 8,14 8,13 "
        "9,0 9,2 9,1 9,3 9,4 9,6 9,5 9,7 9,8 9,10 9,9 9,11 9,12 9,14 9,13 "
        "10,0 10,2 10,1 10,3 10,4 10,6 10,5 10,7 10,8 10,10 10,9 10,11 "
        "10,12 10,14 10,13 11,0 11,2 11,1 11,3 11,4 11,6 11,5 11,7 11,8 "
        "11,10 11,9 11,11 11,12 11,14 11,13 12,0 12,2 12,1 12,3 12,4 12,6 "
        "12,5 12,7 12,8 12,10 12,9 12,11 12,12 12,14 12,13 13,0 13,2 13,1 "
        "13,3 13,4 13,6 13,5 13,7 13,8 13,10 13,9 13,11 13,12 13,14 13,13 "
        "14,0 14,2 14,1 14,3 14,4 14,6 14,5 14,7 14,8 14,10 14,9 14,11 "
        "14,12 14,14 14,13"), -1,
        "draw transcript replays cleanly");
    CHECK_EQ_INT(gm_winner(&g), 3, "full board without five is a draw");
    CHECK(!gm_play(&g, 0, 0), "no moves on a full board");
    int r, c;
    CHECK(!gm_bot(&g, &r, &c), "bot has nothing to suggest in a drawn game");
}

TEST(bot_opening_and_center_tier) {
    check_bot("", 7, 7, "empty board: bot takes the center");
    check_bot("7,7", 6, 6,
              "center gone: nearest ring, scan order picks (6,6)");
    check_bot("7,7 6,6 6,7 7,6", 6, 8,
              "quiet position: first empty cell at Chebyshev distance 1");
}

TEST(bot_takes_the_win) {
    check_bot("7,3 0,0 7,4 0,2 7,5 0,4 7,6 2,0", 7, 2,
              "four in a row: bot completes five at the scan-first end");
    check_bot("5,5 9,4 5,6 9,5 5,7 9,6 5,8 9,7", 5, 4,
              "winning now outranks blocking the opponent's four");
}

TEST(bot_blocks_the_win) {
    check_bot("1,1 9,4 1,3 9,5 3,1 9,6 3,3 9,7", 9, 3,
              "opponent four: bot blocks at the scan-first end");
}

TEST(bot_respects_exact_five_in_threats) {
    check_bot("7,4 0,0 7,5 0,2 7,6 0,4 7,8 2,0 7,9 2,2 7,10 2,4", 7, 3,
              "filling the gap would only make six, so no win exists; "
              "bot falls through to making a four");
    check_bot("0,0 9,5 0,2 9,6 0,4 9,8 2,0 9,9 2,2 9,10 2,4 9,11", 9, 12,
              "the overline gap is not a block; the real five threat is "
              "at the far end");
}

TEST(bot_four_tiers) {
    check_bot("7,5 0,0 7,6 0,2 7,7 0,4", 7, 4,
              "three own stones: bot extends to an exact four, scan-first end");
    check_bot("0,0 10,2 0,2 10,3 0,4 10,4", 10, 1,
              "opponent three: bot caps it at the scan-first end");
}

int main(void) {
    RUN(fresh_board);
    RUN(play_and_alternate);
    RUN(replay_reports_first_bad_move);
    RUN(horizontal_win_black);
    RUN(vertical_win_white);
    RUN(diagonal_wins);
    RUN(overline_is_not_a_win);
    RUN(full_board_draw);
    RUN(bot_opening_and_center_tier);
    RUN(bot_takes_the_win);
    RUN(bot_blocks_the_win);
    RUN(bot_respects_exact_five_in_threats);
    RUN(bot_four_tiers);
    return mt_summary();
}
