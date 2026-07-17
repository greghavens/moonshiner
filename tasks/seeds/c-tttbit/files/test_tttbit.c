/* Acceptance suite for the bitboard tic-tac-toe solver. */
#include "mintest.h"
#include "tttbit.h"

static uint16_t bits3(int a, int b, int c) {
    return (uint16_t)((1u << a) | (1u << b) | (1u << c));
}
static uint16_t bits2(int a, int b) {
    return (uint16_t)((1u << a) | (1u << b));
}

TEST(line_masks_pinned) {
    CHECK_EQ_INT(ttt_line(0), 0x007, "row 0 is cells 0,1,2");
    CHECK_EQ_INT(ttt_line(1), 0x038, "row 1 is cells 3,4,5");
    CHECK_EQ_INT(ttt_line(2), 0x1C0, "row 2 is cells 6,7,8");
    CHECK_EQ_INT(ttt_line(3), 0x049, "col 0 is cells 0,3,6");
    CHECK_EQ_INT(ttt_line(4), 0x092, "col 1 is cells 1,4,7");
    CHECK_EQ_INT(ttt_line(5), 0x124, "col 2 is cells 2,5,8");
    CHECK_EQ_INT(ttt_line(6), 0x111, "main diagonal is cells 0,4,8");
    CHECK_EQ_INT(ttt_line(7), 0x054, "anti diagonal is cells 2,4,6");
    CHECK_EQ_INT(ttt_line(-1), 0, "line index below range yields 0");
    CHECK_EQ_INT(ttt_line(8), 0, "line index above range yields 0");
}

TEST(win_detection) {
    CHECK_EQ_INT(ttt_has_win(0), 0, "empty mask has no win");
    CHECK_EQ_INT(ttt_has_win(bits3(0, 1, 2)), 1, "top row wins");
    CHECK_EQ_INT(ttt_has_win(bits3(2, 4, 6)), 1, "anti diagonal wins");
    CHECK_EQ_INT(ttt_has_win(bits3(1, 4, 7)), 1, "middle column wins");
    CHECK_EQ_INT(ttt_has_win(bits3(0, 1, 3)), 0, "an L shape is not a line");
    CHECK_EQ_INT(ttt_has_win(bits3(0, 4, 7)), 0, "bent three is not a line");
    /* extra marks around a line must not hide it */
    CHECK_EQ_INT(ttt_has_win((uint16_t)(bits3(6, 7, 8) | bits2(0, 4))), 1,
                 "bottom row wins even with extra marks");
    /* five marks, no line among them */
    CHECK_EQ_INT(ttt_has_win((uint16_t)(bits3(0, 1, 5) | bits2(3, 8))), 0,
                 "five scattered marks without a line");
}

TEST(move_generation) {
    CHECK_EQ_INT(ttt_moves(0, 0), 0x1FF, "empty board offers all nine cells");
    CHECK_EQ_INT(ttt_moves(bits2(0, 4), (uint16_t)(1u << 8)),
                 0x1FF & ~(uint16_t)bits3(0, 4, 8),
                 "occupied cells are excluded");
    CHECK_EQ_INT(ttt_moves(bits3(0, 1, 2), bits2(3, 4)), 0,
                 "no moves once X has a line");
    CHECK_EQ_INT(ttt_moves(bits3(1, 5, 6), bits3(0, 4, 8)), 0,
                 "no moves once O has a line");
    /* full-board draw: X on 0,1,4,5,6 / O on 2,3,7,8 — no line either side */
    CHECK_EQ_INT(ttt_moves((uint16_t)(bits3(0, 1, 4) | bits2(5, 6)),
                           (uint16_t)(bits2(2, 3) | bits2(7, 8))), 0,
                 "no moves on a drawn full board");
}

TEST(perfect_play_values) {
    CHECK_EQ_INT(ttt_value(0, 0, 1), 0, "the empty board is a draw");
    for (int i = 0; i < 9; i++) {
        CHECK_EQ_INT(ttt_value((uint16_t)(1u << i), 0, 0), 0,
                     "every opening move still draws");
    }
    /* O replies to X in the center: corners hold the draw, edges lose */
    CHECK_EQ_INT(ttt_value((uint16_t)(1u << 4), (uint16_t)(1u << 0), 1), 0,
                 "corner reply to center holds the draw");
    CHECK_EQ_INT(ttt_value((uint16_t)(1u << 4), (uint16_t)(1u << 2), 1), 0,
                 "corner reply to center holds the draw (cell 2)");
    CHECK_EQ_INT(ttt_value((uint16_t)(1u << 4), (uint16_t)(1u << 6), 1), 0,
                 "corner reply to center holds the draw (cell 6)");
    CHECK_EQ_INT(ttt_value((uint16_t)(1u << 4), (uint16_t)(1u << 8), 1), 0,
                 "corner reply to center holds the draw (cell 8)");
    CHECK_EQ_INT(ttt_value((uint16_t)(1u << 4), (uint16_t)(1u << 1), 1), 1,
                 "edge reply to center loses for O");
    CHECK_EQ_INT(ttt_value((uint16_t)(1u << 4), (uint16_t)(1u << 3), 1), 1,
                 "edge reply to center loses for O (cell 3)");
    CHECK_EQ_INT(ttt_value((uint16_t)(1u << 4), (uint16_t)(1u << 5), 1), 1,
                 "edge reply to center loses for O (cell 5)");
    CHECK_EQ_INT(ttt_value((uint16_t)(1u << 4), (uint16_t)(1u << 7), 1), 1,
                 "edge reply to center loses for O (cell 7)");
    /* O replies to X in a corner: only the center holds */
    for (int i = 1; i < 9; i++) {
        int want = (i == 4) ? 0 : 1;
        CHECK_EQ_INT(ttt_value((uint16_t)(1u << 0), (uint16_t)(1u << i), 1), want,
                     "only the center reply to a corner opening draws");
    }
    /* terminal positions score themselves whoever is nominally to move */
    CHECK_EQ_INT(ttt_value(bits3(0, 1, 2), bits2(3, 4), 0), 1,
                 "X line scores +1");
    CHECK_EQ_INT(ttt_value(bits3(1, 5, 6), bits3(0, 4, 8), 1), -1,
                 "O line scores -1");
    CHECK_EQ_INT(ttt_value((uint16_t)(bits3(0, 1, 4) | bits2(5, 6)),
                           (uint16_t)(bits2(2, 3) | bits2(7, 8)), 1), 0,
                 "full board without a line scores 0");
}

TEST(tactical_values) {
    /* X: 0,1  O: 3,4  X to move — X completes the top row and wins */
    CHECK_EQ_INT(ttt_value(bits2(0, 1), bits2(3, 4), 1), 1,
                 "X to move with an open row wins");
    /* X: 4,8  O: 0,1  X to move — blocking at 2 also builds the anti diagonal */
    CHECK_EQ_INT(ttt_value(bits2(4, 8), bits2(0, 1), 1), 1,
                 "the block at cell 2 turns into a win for X");
    /* X: 0,1  O: 4  O to move — O blocks and holds the draw */
    CHECK_EQ_INT(ttt_value(bits2(0, 1), (uint16_t)(1u << 4), 0), 0,
                 "O to move against a row threat holds the draw");
    /* X: 0,8  O: 4  O to move — the double-corner trap is still a draw */
    CHECK_EQ_INT(ttt_value(bits2(0, 8), (uint16_t)(1u << 4), 0), 0,
                 "double corners vs center is a draw with best play");
    /* X: 4  O: 1  X to move — X punishes the edge reply */
    CHECK_EQ_INT(ttt_value((uint16_t)(1u << 4), (uint16_t)(1u << 1), 1), 1,
                 "center vs edge is winning for X");
}

TEST(best_move_selection) {
    CHECK_EQ_INT(ttt_best(0, 0, 1), 0,
                 "all openings draw so the tie-break picks cell 0");
    /* X: 0,1  O: 3,4  X to move — take the win at 2 */
    CHECK_EQ_INT(ttt_best(bits2(0, 1), bits2(3, 4), 1), 2,
                 "completing the row beats blocking");
    /* X: 0,1  O: 4  O to move — the only non-losing move is the block at 2 */
    CHECK_EQ_INT(ttt_best(bits2(0, 1), (uint16_t)(1u << 4), 0), 2,
                 "O must block the open row");
    /* X: 4,8  O: 0,1  X to move — cell 2 blocks and wins */
    CHECK_EQ_INT(ttt_best(bits2(4, 8), bits2(0, 1), 1), 2,
                 "X's block doubles as the winning move");
    /* X: 4  O: 1  X to move — the winning punishment starts at cell 0 */
    CHECK_EQ_INT(ttt_best((uint16_t)(1u << 4), (uint16_t)(1u << 1), 1), 0,
                 "lowest winning cell is chosen against the edge reply");
    /* X: 0,8  O: 4  O to move — lowest drawing reply is the edge at 1 */
    CHECK_EQ_INT(ttt_best(bits2(0, 8), (uint16_t)(1u << 4), 0), 1,
                 "lowest drawing cell wins the tie-break for O");
    /* finished games have no best move */
    CHECK_EQ_INT(ttt_best(bits3(0, 1, 2), bits2(3, 4), 0), -1,
                 "no best move after X has won");
    CHECK_EQ_INT(ttt_best((uint16_t)(bits3(0, 1, 4) | bits2(5, 6)),
                          (uint16_t)(bits2(2, 3) | bits2(7, 8)), 1), -1,
                 "no best move on a full board");
}

TEST(reachable_state_space) {
    long xw = -1, ow = -1, dr = -1;
    long total = ttt_reachable(&xw, &ow, &dr);
    CHECK_EQ_INT(total, 5478, "distinct reachable positions");
    CHECK_EQ_INT(xw, 626, "reachable positions where X has won");
    CHECK_EQ_INT(ow, 316, "reachable positions where O has won");
    CHECK_EQ_INT(dr, 16, "reachable full-board draws");
    CHECK_EQ_INT(ttt_reachable(NULL, NULL, NULL), 5478,
                 "NULL out-pointers are accepted");
}

int main(void) {
    RUN(line_masks_pinned);
    RUN(win_detection);
    RUN(move_generation);
    RUN(perfect_play_values);
    RUN(tactical_values);
    RUN(best_move_selection);
    RUN(reachable_state_space);
    return mt_summary();
}
