/* Acceptance suite for the minesweeper deduction solver.
 * Expected sets are exact: flat indices row*ncols+col, ascending. */
#include "mintest.h"
#include "minesolver.h"

static int mines[256], safes[256];

static void check_sets(const char *const *rows, int nr, int nc,
                       const int *want_m, int wm,
                       const int *want_s, int ws, const char *msg) {
    int nm = -1, nsf = -1, i;
    int rc = mines_solve(rows, nr, nc, mines, &nm, safes, &nsf);
    CHECK_EQ_INT(rc, 0, msg);
    CHECK_EQ_INT(nm, wm, msg);
    CHECK_EQ_INT(nsf, ws, msg);
    if (rc == 0 && nm == wm) {
        for (i = 0; i < wm; i++)
            CHECK_EQ_INT(mines[i], want_m[i], msg);
    }
    if (rc == 0 && nsf == ws) {
        for (i = 0; i < ws; i++)
            CHECK_EQ_INT(safes[i], want_s[i], msg);
    }
}

TEST(input_validation) {
    const char *ok[] = {"?1", "??"};
    int nm, nsf;
    CHECK_EQ_INT(mines_solve(NULL, 2, 2, mines, &nm, safes, &nsf), -1,
                 "NULL rows rejected");
    CHECK_EQ_INT(mines_solve(ok, 2, 2, NULL, &nm, safes, &nsf), -1,
                 "NULL mine buffer rejected");
    CHECK_EQ_INT(mines_solve(ok, 2, 2, mines, NULL, safes, &nsf), -1,
                 "NULL mine count rejected");
    CHECK_EQ_INT(mines_solve(ok, 2, 2, mines, &nm, NULL, &nsf), -1,
                 "NULL safe buffer rejected");
    CHECK_EQ_INT(mines_solve(ok, 2, 2, mines, &nm, safes, NULL), -1,
                 "NULL safe count rejected");
    CHECK_EQ_INT(mines_solve(ok, 0, 2, mines, &nm, safes, &nsf), -1,
                 "zero rows rejected");
    CHECK_EQ_INT(mines_solve(ok, 17, 2, mines, &nm, safes, &nsf), -1,
                 "too many rows rejected");
    CHECK_EQ_INT(mines_solve(ok, 2, 0, mines, &nm, safes, &nsf), -1,
                 "zero cols rejected");
    CHECK_EQ_INT(mines_solve(ok, 2, 17, mines, &nm, safes, &nsf), -1,
                 "too many cols rejected");
    const char *ragged[] = {"?1", "?"};
    CHECK_EQ_INT(mines_solve(ragged, 2, 2, mines, &nm, safes, &nsf), -1,
                 "short row rejected");
    const char *longrow[] = {"?1", "???"};
    CHECK_EQ_INT(mines_solve(longrow, 2, 2, mines, &nm, safes, &nsf), -1,
                 "long row rejected");
    const char *badch[] = {"?1", "?x"};
    CHECK_EQ_INT(mines_solve(badch, 2, 2, mines, &nm, safes, &nsf), -1,
                 "alien character rejected");
    const char *nine[] = {"?9", "??"};
    CHECK_EQ_INT(mines_solve(nine, 2, 2, mines, &nm, safes, &nsf), -1,
                 "digit 9 rejected");
}

TEST(single_square_rule) {
    /* a flagged mine satisfies both 1s, clearing their other neighbors */
    const char *b1[] = {"1M1", "???"};
    const int s1[] = {3, 4, 5};
    check_sets(b1, 2, 3, NULL, 0, s1, 3,
               "satisfied digits clear the bottom row");
    /* corner 3 with exactly three unknowns: all of them are mines */
    const char *b2[] = {"3?", "??"};
    const int m2[] = {1, 2, 3};
    check_sets(b2, 2, 2, m2, 3, NULL, 0,
               "corner 3 forces all three neighbors");
    /* 2 of 3 unknowns is not a certainty — the solver must not guess */
    const char *b3[] = {"2?", "??"};
    check_sets(b3, 2, 2, NULL, 0, NULL, 0,
               "corner 2 with three unknowns proves nothing");
    /* a lone 1 amid eight unknowns proves nothing either */
    const char *b4[] = {"???", "?1?", "???"};
    check_sets(b4, 3, 3, NULL, 0, NULL, 0,
               "lone 1 stays quiet");
}

TEST(subset_rule_corner) {
    /* the bottom 1s each see {(1,0),(1,1)}; the top-left 1 sees those two
     * plus (0,1) with the same remainder — the difference cell is safe */
    const char *b[] = {"1?", "??", "11"};
    const int s[] = {1};
    check_sets(b, 3, 2, NULL, 0, s, 1,
               "equal-remainder subset proves the extra cell safe");
}

TEST(subset_rule_121_wall) {
    const char *b[] = {"?????", "12321"};
    const int m[] = {1, 2, 3};
    const int s[] = {0, 4};
    check_sets(b, 2, 5, m, 3, s, 2,
               "1-2-3-2-1 wall pins the three center mines");
}

TEST(subset_rule_1221_wall) {
    const char *b[] = {"????", "1221"};
    const int m[] = {1, 2};
    const int s[] = {0, 3};
    check_sets(b, 2, 4, m, 2, s, 2,
               "1-2-2-1 wall pins the two center mines");
}

TEST(mixed_board_fixpoint) {
    const char *b[] = {"0001?",
                       "0001?",
                       "1101?",
                       "?101?",
                       "?211?",
                       "????1"};
    const int m[] = {9, 15, 24, 26};
    const int s[] = {4, 14, 19, 20, 25, 27, 28};
    check_sets(b, 6, 5, m, 4, s, 7,
               "cascading deductions settle the whole frontier");
}

TEST(premarked_mines_not_reported) {
    /* the flagged mine satisfies half of the 2; the zeros clear row 1 and
     * the remaining unknown corner is then forced — but the M itself must
     * never appear in the output */
    const char *b[] = {"M2?", "??1", "000"};
    const int m[] = {2};
    const int s[] = {3, 4};
    check_sets(b, 3, 3, m, 1, s, 2,
               "only newly deduced cells are reported");
}

TEST(contradiction_detection) {
    int nm, nsf;
    /* a 1 with no unknown neighbors and no flagged mine cannot be satisfied */
    const char *b1[] = {"11", "11"};
    CHECK_EQ_INT(mines_solve(b1, 2, 2, mines, &nm, safes, &nsf), -1,
                 "unsatisfiable 1 reports a misread board");
    /* remainder above the unknown count from the start */
    const char *b2[] = {"3?", "?0"};
    CHECK_EQ_INT(mines_solve(b2, 2, 2, mines, &nm, safes, &nsf), -1,
                 "3 with only two candidate cells reports a misread board");
    /* contradiction that only appears AFTER propagation: the zero row clears
     * the middle row, then the 1 and the 2 disagree about the last cell */
    const char *b3[] = {"1?2", "???", "000"};
    CHECK_EQ_INT(mines_solve(b3, 3, 3, mines, &nm, safes, &nsf), -1,
                 "conflicting counts surface after propagation");
}

int main(void) {
    RUN(input_validation);
    RUN(single_square_rule);
    RUN(subset_rule_corner);
    RUN(subset_rule_121_wall);
    RUN(subset_rule_1221_wall);
    RUN(mixed_board_fixpoint);
    RUN(premarked_mines_not_reported);
    RUN(contradiction_detection);
    return mt_summary();
}
