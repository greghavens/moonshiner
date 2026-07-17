/* Acceptance suite for the union-find maze module.
 * Renders and follower traces are byte-exact per seed. */
#include "mintest.h"
#include "mazeuf.h"

TEST(union_find_behavior) {
    uf u;
    CHECK_EQ_INT(uf_init(&u, 0), -1, "zero elements rejected");
    CHECK_EQ_INT(uf_init(&u, 257), -1, "too many elements rejected");
    CHECK_EQ_INT(uf_init(NULL, 10), -1, "NULL structure rejected");
    CHECK_EQ_INT(uf_init(&u, 10), 0, "ten singletons initialize");
    CHECK(uf_find(&u, 2) != uf_find(&u, 3), "fresh elements are separate");
    CHECK_EQ_INT(uf_find(&u, 10), -1, "find out of range rejected");
    CHECK_EQ_INT(uf_find(&u, -1), -1, "negative find rejected");
    CHECK_EQ_INT(uf_union(&u, 2, 3), 1, "first union merges");
    CHECK_EQ_INT(uf_union(&u, 2, 3), 0, "repeat union reports already joined");
    CHECK_EQ_INT(uf_union(&u, 3, 2), 0, "order does not matter");
    CHECK(uf_find(&u, 2) == uf_find(&u, 3), "merged elements share a root");
    CHECK_EQ_INT(uf_union(&u, 4, 5), 1, "second pair merges");
    CHECK_EQ_INT(uf_union(&u, 3, 4), 1, "bridging the pairs merges");
    CHECK(uf_find(&u, 2) == uf_find(&u, 5), "connectivity is transitive");
    CHECK(uf_find(&u, 0) != uf_find(&u, 2), "untouched element stays apart");
    CHECK_EQ_INT(uf_union(&u, 0, 10), -1, "union out of range rejected");
    {
        /* after joining everything there is exactly one representative */
        int i, reps = 0;
        for (i = 1; i < 10; i++) uf_union(&u, 0, i);
        for (i = 0; i < 10; i++)
            if (uf_find(&u, i) == uf_find(&u, 0)) reps++;
        CHECK_EQ_INT(reps, 10, "all ten elements collapse into one set");
    }
}

TEST(generate_validation) {
    maze m;
    CHECK_EQ_INT(maze_generate(NULL, 5, 4, 1), -1, "NULL maze rejected");
    CHECK_EQ_INT(maze_generate(&m, 1, 4, 1), -1, "width 1 rejected");
    CHECK_EQ_INT(maze_generate(&m, 17, 4, 1), -1, "width 17 rejected");
    CHECK_EQ_INT(maze_generate(&m, 5, 1, 1), -1, "height 1 rejected");
    CHECK_EQ_INT(maze_generate(&m, 5, 17, 1), -1, "height 17 rejected");
    CHECK_EQ_INT(maze_generate(&m, 5, 4, 1), 0, "5x4 generates");
    CHECK_EQ_INT(m.w, 5, "width recorded");
    CHECK_EQ_INT(m.h, 4, "height recorded");
}

static int passage_count(const maze *m) {
    int r, c, n = 0;
    for (r = 0; r < m->h; r++)
        for (c = 0; c < m->w; c++)
            n += m->open_e[r][c] + m->open_s[r][c];
    return n;
}

TEST(spanning_tree_property) {
    maze m;
    uint32_t seed;
    for (seed = 1; seed <= 5; seed++) {
        CHECK_EQ_INT(maze_generate(&m, 7, 6, seed), 0, "7x6 generates");
        CHECK_EQ_INT(passage_count(&m), 41,
                     "a perfect maze has cells-1 passages");
    }
}

TEST(render_5x4_seed1) {
    maze m;
    char buf[256];
    CHECK_EQ_INT(maze_generate(&m, 5, 4, 1), 0, "5x4 seed 1 generates");
    CHECK_EQ_INT(maze_render(&m, buf, sizeof buf), 108,
                 "render length is (2h+1)*(2w+2)");
    CHECK_EQ_STR(buf,
        "###########\n"
        "#         #\n"
        "######### #\n"
        "#   #   # #\n"
        "### # ### #\n"
        "#     # # #\n"
        "# ### # # #\n"
        "#   #     #\n"
        "###########\n",
        "5x4 seed-1 render is pinned");
    CHECK_EQ_INT(maze_render(&m, buf, 108), -1,
                 "cap without room for the NUL fails");
    CHECK_EQ_INT(maze_render(&m, buf, 109), 108, "exact-fit cap succeeds");
}

TEST(render_6x5_seed42) {
    maze m;
    char buf[256];
    CHECK_EQ_INT(maze_generate(&m, 6, 5, 42), 0, "6x5 seed 42 generates");
    CHECK_EQ_INT(maze_render(&m, buf, sizeof buf), 154, "render length checks");
    CHECK_EQ_STR(buf,
        "#############\n"
        "#     #   # #\n"
        "# ##### ### #\n"
        "#           #\n"
        "### ### ### #\n"
        "#   #   #   #\n"
        "##### # # # #\n"
        "# #   # # # #\n"
        "# ### # ### #\n"
        "#     # #   #\n"
        "#############\n",
        "6x5 seed-42 render is pinned");
}

TEST(render_4x4_seed7) {
    maze m;
    char buf[256];
    CHECK_EQ_INT(maze_generate(&m, 4, 4, 7), 0, "4x4 seed 7 generates");
    CHECK_EQ_INT(maze_render(&m, buf, sizeof buf), 90, "render length checks");
    CHECK_EQ_STR(buf,
        "#########\n"
        "# # # # #\n"
        "# # # # #\n"
        "#     # #\n"
        "### ### #\n"
        "#     # #\n"
        "### # # #\n"
        "#   #   #\n"
        "#########\n",
        "4x4 seed-7 render is pinned");
}

TEST(same_seed_same_maze) {
    maze a, b;
    char ra[256], rb[256];
    CHECK_EQ_INT(maze_generate(&a, 6, 5, 42), 0, "first burn");
    CHECK_EQ_INT(maze_generate(&b, 6, 5, 42), 0, "second burn");
    maze_render(&a, ra, sizeof ra);
    maze_render(&b, rb, sizeof rb);
    CHECK_EQ_STR(ra, rb, "paired props carve the identical maze");
}

TEST(wall_follower_traces) {
    maze m;
    char dirs[512];
    CHECK_EQ_INT(maze_generate(&m, 5, 4, 1), 0, "5x4 seed 1 generates");
    CHECK_EQ_INT(maze_solve(&m, dirs, sizeof dirs), 7, "trace length 5x4 seed 1");
    CHECK_EQ_STR(dirs, "EEEESSS", "5x4 seed-1 trace is pinned");
    CHECK_EQ_INT(maze_solve(&m, dirs, 7), -1, "cap without room for NUL fails");
    CHECK_EQ_INT(maze_solve(&m, dirs, 8), 7, "exact-fit cap succeeds");

    CHECK_EQ_INT(maze_generate(&m, 6, 5, 42), 0, "6x5 seed 42 generates");
    CHECK_EQ_INT(maze_solve(&m, dirs, sizeof dirs), 37, "trace length 6x5 seed 42");
    CHECK_EQ_STR(dirs, "SESWENEESWSWESWWNSEENNESSNNNEESWSNESS",
                 "6x5 seed-42 trace keeps the dead-end walks");

    CHECK_EQ_INT(maze_generate(&m, 4, 4, 7), 0, "4x4 seed 7 generates");
    CHECK_EQ_INT(maze_solve(&m, dirs, sizeof dirs), 12, "trace length 4x4 seed 7");
    CHECK_EQ_STR(dirs, "SESWESWENESE", "4x4 seed-7 trace is pinned");
}

TEST(tiny_maze) {
    maze m;
    char buf[64], dirs[64];
    CHECK_EQ_INT(maze_generate(&m, 2, 2, 3), 0, "2x2 seed 3 generates");
    CHECK_EQ_INT(passage_count(&m), 3, "three passages in a 2x2 tree");
    maze_render(&m, buf, sizeof buf);
    CHECK_EQ_STR(buf,
        "#####\n"
        "#   #\n"
        "# # #\n"
        "# # #\n"
        "#####\n",
        "2x2 seed-3 render is pinned");
    CHECK_EQ_INT(maze_solve(&m, dirs, sizeof dirs), 4, "2x2 trace length");
    CHECK_EQ_STR(dirs, "SNES", "the follower backs out of the dead end");
}

int main(void) {
    RUN(union_find_behavior);
    RUN(generate_validation);
    RUN(spanning_tree_property);
    RUN(render_5x4_seed1);
    RUN(render_6x5_seed42);
    RUN(render_4x4_seed7);
    RUN(same_seed_same_maze);
    RUN(tiny_maze);
    RUN(wall_follower_traces);
    return mt_summary();
}
