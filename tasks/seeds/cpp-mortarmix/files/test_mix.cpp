#include "mintest.h"
#include "mix.h"

TEST(bricks_scale_with_courses) {
    CHECK_EQ_INT(bricks_for_wall(12, 38), 456, "garden wall");
    CHECK_EQ_INT(bricks_for_wall(1, 1), 1, "single brick pillar");
    CHECK_EQ_INT(bricks_for_wall(0, 50), 0, "no courses, no bricks");
    CHECK_EQ_INT(bricks_for_wall(200, 120), 24000, "boundary wall");
}

TEST(mortar_weight_is_exact) {
    CHECK(mortar_kg(456) == 230.0, "garden wall mortar");
    CHECK(mortar_kg(1) == 2.5, "one brick still needs the primer bed");
    CHECK(mortar_kg(0) == 0.0, "no bricks, no mortar");
    CHECK(mortar_kg(24000) == 12002.0, "boundary wall mortar");
}

TEST(bags_round_up_never_down) {
    CHECK_EQ_INT(bags_needed(230.0), 10, "9.2 bags becomes 10");
    CHECK_EQ_INT(bags_needed(225.0), 9, "exact multiple stays exact");
    CHECK_EQ_INT(bags_needed(0.1), 1, "a smear still opens a bag");
    CHECK_EQ_INT(bags_needed(0.0), 0, "nothing to mix");
}

TEST(water_per_bag) {
    CHECK_EQ_INT(water_ml(10), 42000, "ten bags");
    CHECK_EQ_INT(water_ml(0), 0, "dry yard");
}

TEST(mixer_batches_round_up) {
    CHECK_EQ_INT(mixer_batches(10), 4, "ten bags is four drum loads");
    CHECK_EQ_INT(mixer_batches(9), 3, "nine bags fits three exactly");
    CHECK_EQ_INT(mixer_batches(1), 1, "one bag still turns the drum");
    CHECK_EQ_INT(mixer_batches(0), 0, "no bags, no batches");
}

TEST(pallet_remainder_counts_leftovers) {
    CHECK_EQ_INT(pallet_remainder(456), 44, "one pallet leaves 44");
    CHECK_EQ_INT(pallet_remainder(500), 0, "exact pallet");
    CHECK_EQ_INT(pallet_remainder(501), 499, "one brick into pallet two");
    CHECK_EQ_INT(pallet_remainder(0), 0, "no wall, no leftovers");
}

int main() {
    RUN(bricks_scale_with_courses);
    RUN(mortar_weight_is_exact);
    RUN(bags_round_up_never_down);
    RUN(water_per_bag);
    RUN(mixer_batches_round_up);
    RUN(pallet_remainder_counts_leftovers);
    return mt_summary();
}
