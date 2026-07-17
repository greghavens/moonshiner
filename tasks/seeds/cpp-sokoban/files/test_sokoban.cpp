/* Acceptance suite for the sokoban engine.
 * Renders, return codes and deadlock verdicts were checked against an
 * independent implementation; the pinned strings are the contract. */
#include "mintest.h"
#include <string>

#include "sokoban.h"

static const char *ONE_PUSH =
    "#####\n"
    "#@$.#\n"
    "#####\n";

static const char *CORNER_TRAP =
    "#####\n"
    "#.  #\n"
    "#  $#\n"
    "#@  #\n"
    "#####\n";

static const char *ALREADY_HOME =
    "####\n"
    "#*@#\n"
    "####\n";

static const char *WALL_SQUEEZE =
    "#####\n"
    "#.@$#\n"
    "#####\n";

static const char *TWO_BOX_YARD =
    "########\n"
    "#      #\n"
    "# $ @  #\n"
    "# .# $ #\n"
    "#    . #\n"
    "########\n";

static const char *BOX_TRAIN =
    "#######\n"
    "#@$$..#\n"
    "#######\n";

TEST(load_validation) {
    Sokoban s;
    CHECK(sb_load(&s, ONE_PUSH), "well-formed level loads");
    CHECK(!sb_load(&s, "#####\n#$ .#\n#####\n"), "no player rejected");
    CHECK(!sb_load(&s, "#####\n#@@$.#\n#####\n"), "two players rejected");
    CHECK(!sb_load(&s, "#####\n#@$ #\n#####\n"), "one box, no goal rejected");
    CHECK(!sb_load(&s, "#####\n#@$..#\n#####\n"), "goal surplus rejected");
    CHECK(!sb_load(&s, ""), "empty level rejected");
}

TEST(single_push_to_win) {
    Sokoban s;
    CHECK(sb_load(&s, ONE_PUSH), "level loads");
    CHECK(!sb_solved(&s), "not solved at the start");
    CHECK_EQ_INT(sb_move(&s, 'r'), 2, "pushing the box reports a push");
    CHECK(sb_solved(&s), "box on the goal solves the level");
    CHECK(!sb_deadlocked(&s), "a solved level is not deadlocked");
    std::string r1 = sb_render(&s);
    CHECK_EQ_STR(r1.c_str(), "#####\n# @*#\n#####\n",
                 "box renders as * on the goal");
    CHECK_EQ_INT(sb_moves(&s), 1, "one move on the clock");
    CHECK_EQ_INT(sb_pushes(&s), 1, "one push on the clock");
}

TEST(push_chain_is_blocked) {
    Sokoban s;
    CHECK(sb_load(&s, BOX_TRAIN), "level loads");
    CHECK_EQ_INT(sb_move(&s, 'r'), 0, "a box behind a box does not budge");
    CHECK_EQ_INT(sb_run(&s, "rr"), 0, "run stops at the first blocked move");
    CHECK_EQ_INT(sb_moves(&s), 0, "blocked moves never reach the history");
    CHECK(!sb_undo(&s), "nothing to undo");
}

TEST(walls_walking_and_letters) {
    Sokoban s;
    CHECK(sb_load(&s, WALL_SQUEEZE), "level loads");
    CHECK(sb_deadlocked(&s), "box against the right wall corner is dead on arrival");
    CHECK_EQ_INT(sb_move(&s, 'r'), 0, "pushing into a wall is blocked");
    CHECK_EQ_INT(sb_move(&s, 'l'), 1, "walking is reported as a walk");
    std::string r2 = sb_render(&s);
    CHECK_EQ_STR(r2.c_str(), "#####\n#+ $#\n#####\n",
                 "player renders as + on a goal");
    CHECK_EQ_INT(sb_move(&s, 'l'), 0, "walking into a wall is blocked");
    CHECK_EQ_INT(sb_move(&s, 'x'), -1, "unknown letter is an error, not a block");
    CHECK_EQ_INT(sb_moves(&s), 1, "only the walk was recorded");
    CHECK_EQ_INT(sb_pushes(&s), 0, "no pushes recorded");
}

TEST(corner_deadlock_and_undo) {
    Sokoban s;
    CHECK(sb_load(&s, CORNER_TRAP), "level loads");
    CHECK(!sb_deadlocked(&s), "box on the open wall edge is still alive");
    CHECK_EQ_INT(sb_run(&s, "rru"), 3, "walk, walk, push all apply");
    CHECK_EQ_INT(sb_pushes(&s), 1, "one push so far");
    CHECK(sb_deadlocked(&s), "box pushed into the corner is dead");
    std::string r3 = sb_render(&s);
    CHECK_EQ_STR(r3.c_str(),
                 "#####\n#. $#\n#  @#\n#   #\n#####\n",
                 "post-push board renders exactly");
    CHECK(sb_undo(&s), "undo the push");
    CHECK(!sb_deadlocked(&s), "undo brings the box back to life");
    CHECK_EQ_INT(sb_pushes(&s), 0, "undo returns the push to the till");
    CHECK_EQ_INT(sb_moves(&s), 2, "two walks remain");
}

TEST(goal_corner_is_not_a_deadlock) {
    Sokoban s;
    CHECK(sb_load(&s, ALREADY_HOME), "level loads");
    CHECK(sb_solved(&s), "box starts on its goal");
    CHECK(!sb_deadlocked(&s), "a cornered box ON a goal is fine");
}

TEST(two_box_solve_with_renders) {
    Sokoban s;
    CHECK(sb_load(&s, TWO_BOX_YARD), "level loads");
    std::string r4 = sb_render(&s);
    CHECK_EQ_STR(r4.c_str(), TWO_BOX_YARD,
                 "initial render reproduces the level");
    CHECK_EQ_INT(sb_run(&s, "ulld"), 4, "route to the first box applies fully");
    std::string r5 = sb_render(&s);
    CHECK_EQ_STR(r5.c_str(),
                 "########\n"
                 "#      #\n"
                 "# @    #\n"
                 "# *# $ #\n"
                 "#    . #\n"
                 "########\n",
                 "first box parked on its goal");
    CHECK_EQ_INT(sb_run(&s, "rrrd"), 4, "route to the second box applies fully");
    CHECK(sb_solved(&s), "both boxes home");
    std::string r6 = sb_render(&s);
    CHECK_EQ_STR(r6.c_str(),
                 "########\n"
                 "#      #\n"
                 "#      #\n"
                 "# *# @ #\n"
                 "#    * #\n"
                 "########\n",
                 "solved board renders exactly");
    CHECK_EQ_INT(sb_moves(&s), 8, "eight moves recorded");
    CHECK_EQ_INT(sb_pushes(&s), 2, "two of them pushes");
    int undos = 0;
    while (sb_undo(&s)) undos++;
    CHECK_EQ_INT(undos, 8, "undo unwinds every recorded move");
    std::string r7 = sb_render(&s);
    CHECK_EQ_STR(r7.c_str(), TWO_BOX_YARD,
                 "full undo restores the initial board byte for byte");
    CHECK_EQ_INT(sb_moves(&s), 0, "move counter back to zero");
    CHECK_EQ_INT(sb_pushes(&s), 0, "push counter back to zero");
    CHECK(!sb_solved(&s), "and the level is unsolved again");
}

TEST(uppercase_lurd_accepted) {
    Sokoban s;
    CHECK(sb_load(&s, TWO_BOX_YARD), "level loads");
    CHECK_EQ_INT(sb_run(&s, "ULLD"), 4, "uppercase LURD works the same");
    CHECK_EQ_INT(sb_move(&s, 'R'), 1, "single uppercase letter too");
    CHECK_EQ_INT(sb_pushes(&s), 1, "the push counted");
}

int main(void) {
    RUN(load_validation);
    RUN(single_push_to_win);
    RUN(push_chain_is_blocked);
    RUN(walls_walking_and_letters);
    RUN(corner_deadlock_and_undo);
    RUN(goal_corner_is_not_a_deadlock);
    RUN(two_box_solve_with_renders);
    RUN(uppercase_lurd_accepted);
    return mt_summary();
}
