#include <map>
#include <utility>

#include "mintest.h"
#include "pattern.h"
#include "quilt.h"

/* 3x2 sampler:  5 2 7
 *               2 0 5   (0 = unpieced) */
static PatchGrid sampler() {
    PatchGrid g(3, 2);
    g.set(0, 0, 5); g.set(1, 0, 2); g.set(2, 0, 7);
    g.set(0, 1, 2); g.set(1, 1, 0); g.set(2, 1, 5);
    return g;
}

TEST(mirror_flips_left_to_right) {
    PatchGrid g = sampler();
    PatchGrid m = mirrored(g);
    CHECK_EQ_INT(m.at(0, 0), 7, "top-right patch lands on the left");
    CHECK_EQ_INT(m.at(1, 0), 2, "centre column stays put");
    CHECK_EQ_INT(m.at(2, 0), 5, "top-left patch lands on the right");
    CHECK_EQ_INT(m.at(0, 1), 5, "bottom row mirrors too");
    CHECK_EQ_INT(m.at(1, 1), 0, "unpieced cell mirrors as unpieced");
    CHECK_EQ_INT(m.at(2, 1), 2, "bottom-left lands bottom-right");
    CHECK_EQ_INT(g.at(0, 0), 5, "original quilt is untouched");
    CHECK_EQ_INT(g.at(2, 1), 5, "original bottom row is untouched");
}

TEST(mirroring_twice_restores_the_top) {
    PatchGrid g = sampler();
    PatchGrid mm = mirrored(mirrored(g));
    for (int y = 0; y < 2; y++)
        for (int x = 0; x < 3; x++)
            CHECK_EQ_INT(mm.at(x, y), g.at(x, y), "double mirror is identity");
}

TEST(grids_copy_independently) {
    PatchGrid a = sampler();
    PatchGrid b = a;
    b.set(0, 0, 9);
    CHECK_EQ_INT(a.at(0, 0), 5, "copy construction does not alias");
    PatchGrid c(1, 1);
    c = a;
    c.set(2, 0, 4);
    CHECK_EQ_INT(a.at(2, 0), 7, "copy assignment does not alias");
    CHECK_EQ_INT(c.at(0, 1), 2, "assigned grid holds the full layout");
}

TEST(moved_grid_keeps_the_layout) {
    PatchGrid tmp = sampler();
    PatchGrid m = std::move(tmp);
    CHECK_EQ_INT(m.at(2, 0), 7, "moved-to grid has the sampler layout");
    CHECK_EQ_INT(m.shape().first, 3, "width survives the move");
    CHECK_EQ_INT(m.shape().second, 2, "height survives the move");
}

TEST(palette_counts_skip_unpieced_cells) {
    std::map<int, int> counts = palette_counts(sampler());
    CHECK_EQ_INT(counts.size(), 3, "three palette codes in use");
    CHECK_EQ_INT(counts[5], 2, "two patches of code 5");
    CHECK_EQ_INT(counts[2], 2, "two patches of code 2");
    CHECK_EQ_INT(counts[7], 1, "one patch of code 7");
    CHECK_EQ_INT(counts.count(0), 0, "unpieced cells are not a color");
}

TEST(dominant_color_breaks_ties_low) {
    CHECK_EQ_INT(dominant_color(sampler()), 2,
                 "codes 2 and 5 tie at two patches; smaller code wins");
    PatchGrid empty(2, 2);
    CHECK_EQ_INT(dominant_color(empty), 0, "unpieced top has no dominant code");
}

int main() {
    RUN(mirror_flips_left_to_right);
    RUN(mirroring_twice_restores_the_top);
    RUN(grids_copy_independently);
    RUN(moved_grid_keeps_the_layout);
    RUN(palette_counts_skip_unpieced_cells);
    RUN(dominant_color_breaks_ties_low);
    return mt_summary();
}
