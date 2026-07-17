/* Acceptance suite for the fixed-memory snake engine.
 * Frames and tick sequences are exact-match: the badge replays them. */
#include "mintest.h"
#include "snake.h"

static void check_frame(const snake *s, const char *want, const char *msg) {
    char buf[512];
    int n = snake_render(s, buf, sizeof buf);
    CHECK_EQ_INT(n, (int)strlen(want), msg);
    CHECK_EQ_STR(buf, want, msg);
}

TEST(init_validation) {
    snake s;
    uint8_t food[] = {5, 2};
    CHECK_EQ_INT(snake_init(NULL, 6, 5, food, 1), -1, "NULL game rejected");
    CHECK_EQ_INT(snake_init(&s, 3, 5, food, 1), -1, "width below 4 rejected");
    CHECK_EQ_INT(snake_init(&s, 17, 5, food, 1), -1, "width above 16 rejected");
    CHECK_EQ_INT(snake_init(&s, 6, 3, food, 1), -1, "height below 4 rejected");
    CHECK_EQ_INT(snake_init(&s, 6, 17, food, 1), -1, "height above 16 rejected");
    CHECK_EQ_INT(snake_init(&s, 6, 5, NULL, 1), -1, "NULL queue with nfood > 0 rejected");
    CHECK_EQ_INT(snake_init(&s, 6, 5, food, -1), -1, "negative nfood rejected");
    CHECK_EQ_INT(snake_init(&s, 6, 5, food, 33), -1, "nfood above 32 rejected");
    uint8_t off[] = {6, 2};
    CHECK_EQ_INT(snake_init(&s, 6, 5, off, 1), -1, "food off the grid rejected");
    uint8_t offy[] = {2, 5};
    CHECK_EQ_INT(snake_init(&s, 6, 5, offy, 1), -1, "food row off the grid rejected");
    CHECK_EQ_INT(snake_init(&s, 6, 5, NULL, 0), 0, "empty queue is fine");
    CHECK_EQ_INT(snake_init(&s, 6, 5, food, 1), 0, "valid init succeeds");
    CHECK_EQ_INT(snake_len(&s), 3, "snake starts at length 3");
    CHECK_EQ_INT(snake_score(&s), 0, "score starts at 0");
    CHECK_EQ_INT(snake_alive(&s), 1, "snake starts alive");
}

TEST(scripted_game_frames) {
    snake s;
    uint8_t food[] = {5, 2, 5, 4, 0, 0};
    CHECK_EQ_INT(snake_init(&s, 6, 5, food, 3), 0, "init 6x5 with 3 queued foods");
    check_frame(&s,
        "########\n"
        "#......#\n"
        "#......#\n"
        "#.ooO.*#\n"
        "#......#\n"
        "#......#\n"
        "########\n",
        "initial frame");
    CHECK_EQ_INT(snake_tick(&s), 1, "plain move east");
    check_frame(&s,
        "########\n"
        "#......#\n"
        "#......#\n"
        "#..ooO*#\n"
        "#......#\n"
        "#......#\n"
        "########\n",
        "frame after one tick east");
    CHECK_EQ_INT(snake_tick(&s), 2, "eating tick reports 2");
    CHECK_EQ_INT(snake_score(&s), 1, "score after first food");
    CHECK_EQ_INT(snake_len(&s), 4, "length grows to 4");
    check_frame(&s,
        "########\n"
        "#......#\n"
        "#......#\n"
        "#..oooO#\n"
        "#......#\n"
        "#.....*#\n"
        "########\n",
        "frame after first food: next food appears");
    CHECK_EQ_INT(snake_turn(&s, 'S'), 0, "turn south accepted");
    CHECK_EQ_INT(snake_tick(&s), 1, "plain move south");
    check_frame(&s,
        "########\n"
        "#......#\n"
        "#......#\n"
        "#...ooo#\n"
        "#.....O#\n"
        "#.....*#\n"
        "########\n",
        "frame heading south");
    CHECK_EQ_INT(snake_tick(&s), 2, "second food eaten");
    check_frame(&s,
        "########\n"
        "#*.....#\n"
        "#......#\n"
        "#...ooo#\n"
        "#.....o#\n"
        "#.....O#\n"
        "########\n",
        "frame after second food: third food appears");
    CHECK_EQ_INT(snake_turn(&s, 'W'), 0, "turn west accepted");
    CHECK_EQ_INT(snake_tick(&s), 1, "plain move west");
    check_frame(&s,
        "########\n"
        "#*.....#\n"
        "#......#\n"
        "#....oo#\n"
        "#.....o#\n"
        "#....Oo#\n"
        "########\n",
        "frame heading west along the bottom");
    CHECK_EQ_INT(snake_turn(&s, 'E'), -1, "reversing into the neck is rejected");
    CHECK_EQ_INT(snake_turn(&s, 'X'), -1, "unknown direction is rejected");
    CHECK_EQ_INT(snake_score(&s), 2, "score is 2 after the script");
    CHECK_EQ_INT(snake_len(&s), 5, "length is 5 after the script");
    CHECK_EQ_INT(snake_alive(&s), 1, "snake is still alive");
}

TEST(wall_death) {
    snake s;
    CHECK_EQ_INT(snake_init(&s, 6, 5, NULL, 0), 0, "init without food");
    CHECK_EQ_INT(snake_tick(&s), 1, "move 1 east");
    CHECK_EQ_INT(snake_tick(&s), 1, "move 2 east reaches the wall column");
    CHECK_EQ_INT(snake_tick(&s), 0, "tick into the wall kills");
    CHECK_EQ_INT(snake_alive(&s), 0, "snake is dead");
    CHECK_EQ_INT(snake_tick(&s), -1, "ticking a dead snake returns -1");
    CHECK_EQ_INT(snake_turn(&s, 'N'), -1, "turning a dead snake is rejected");
    check_frame(&s,
        "########\n"
        "#......#\n"
        "#......#\n"
        "#...ooO#\n"
        "#......#\n"
        "#......#\n"
        "########\n",
        "death frame stays frozen at the last legal position");
}

TEST(tail_chase_is_legal) {
    snake s;
    uint8_t food[] = {4, 2, 1, 1};
    CHECK_EQ_INT(snake_init(&s, 6, 5, food, 2), 0, "init with food directly ahead");
    CHECK_EQ_INT(snake_tick(&s), 2, "eat immediately, length 4");
    CHECK_EQ_INT(snake_turn(&s, 'N'), 0, "turn north");
    CHECK_EQ_INT(snake_tick(&s), 1, "move north");
    CHECK_EQ_INT(snake_turn(&s, 'W'), 0, "turn west");
    CHECK_EQ_INT(snake_tick(&s), 1, "move west");
    CHECK_EQ_INT(snake_turn(&s, 'S'), 0, "turn south");
    CHECK_EQ_INT(snake_tick(&s), 1, "moving into the vacating tail cell is legal");
    CHECK_EQ_INT(snake_alive(&s), 1, "snake survives the tail chase");
    check_frame(&s,
        "########\n"
        "#......#\n"
        "#.*.oo.#\n"
        "#...Oo.#\n"
        "#......#\n"
        "#......#\n"
        "########\n",
        "frame after the tail-chase loop");
}

TEST(self_collision_death) {
    snake s;
    uint8_t food[] = {5, 3, 5, 2, 4, 2};
    CHECK_EQ_INT(snake_init(&s, 8, 6, food, 3), 0, "init 8x6 with a food ladder");
    CHECK_EQ_INT(snake_tick(&s), 2, "eat food 1");
    CHECK_EQ_INT(snake_turn(&s, 'N'), 0, "turn north");
    CHECK_EQ_INT(snake_tick(&s), 2, "eat food 2");
    CHECK_EQ_INT(snake_turn(&s, 'W'), 0, "turn west");
    CHECK_EQ_INT(snake_tick(&s), 2, "eat food 3");
    CHECK_EQ_INT(snake_turn(&s, 'S'), 0, "turn south");
    CHECK_EQ_INT(snake_tick(&s), 0, "curling back into the body kills");
    CHECK_EQ_INT(snake_alive(&s), 0, "snake is dead");
    CHECK_EQ_INT(snake_score(&s), 3, "score kept at 3");
    CHECK_EQ_INT(snake_len(&s), 6, "length kept at 6");
    check_frame(&s,
        "##########\n"
        "#........#\n"
        "#........#\n"
        "#....Oo..#\n"
        "#..oooo..#\n"
        "#........#\n"
        "#........#\n"
        "##########\n",
        "self-collision frame stays frozen");
}

TEST(turn_bookkeeping) {
    snake s;
    CHECK_EQ_INT(snake_init(&s, 8, 6, NULL, 0), 0, "init 8x6");
    /* heading east; N then S are both accepted against the moved direction,
     * and the last accepted turn wins */
    CHECK_EQ_INT(snake_turn(&s, 'N'), 0, "north accepted while moving east");
    CHECK_EQ_INT(snake_turn(&s, 'S'), 0, "south also accepted (checked vs moved dir)");
    CHECK_EQ_INT(snake_tick(&s), 1, "tick applies the last accepted turn");
    /* head started at (4,3): after the southward tick it is at (4,4) */
    check_frame(&s,
        "##########\n"
        "#........#\n"
        "#........#\n"
        "#........#\n"
        "#...oo...#\n"
        "#....O...#\n"
        "#........#\n"
        "##########\n",
        "south wins as the last accepted turn");
    CHECK_EQ_INT(snake_turn(&s, 'N'), -1, "north now reverses the moved direction");
}

TEST(render_buffer_contract) {
    snake s;
    CHECK_EQ_INT(snake_init(&s, 6, 5, NULL, 0), 0, "init 6x5");
    /* frame is (5+2) rows x (6+3) chars = 63 chars + NUL */
    char buf[64];
    CHECK_EQ_INT(snake_render(&s, buf, 63), -1, "cap without room for the NUL fails");
    CHECK_EQ_INT(snake_render(&s, buf, 64), 63, "exact-fit cap succeeds");
    CHECK_EQ_INT((int)strlen(buf), 63, "buffer is NUL-terminated");
}

int main(void) {
    RUN(init_validation);
    RUN(scripted_game_frames);
    RUN(wall_death);
    RUN(tail_chase_is_legal);
    RUN(self_collision_death);
    RUN(turn_bookkeeping);
    RUN(render_buffer_contract);
    return mt_summary();
}
