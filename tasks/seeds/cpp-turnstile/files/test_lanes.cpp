/* Acceptance tests for the entrance lane bank. Build and run with
 * `make test`. Admission counts, bounce tracking, board lines, close-out
 * resets and hardware replacement are pinned exactly. */
#include "mintest.h"

#include "lane.h"
#include "card_lane.h"
#include "bank.h"

#include <memory>
#include <string>

TEST(plain_lane_counts_admissions) {
    Lane lane;

    CHECK_EQ_INT(lane.admit(4), 4, "everyone gets through a plain lane");
    CHECK_EQ_INT(lane.admit(-3), 0, "nonsense party sizes admit nobody");
    CHECK_EQ_INT(lane.passed(), 4, "counter tracks the day");

    const std::string line = lane.status();

    CHECK_EQ_STR(line.c_str(), "lane open, 4 through", "plain board line");
}

TEST(card_lane_bounces_every_third_swipe) {
    CardLane lane;

    CHECK_EQ_INT(lane.admit(7), 5, "seven swipes, two bounce");
    CHECK_EQ_INT(lane.admit(3), 2, "three swipes, one bounces");
    CHECK_EQ_INT(lane.passed(), 7, "passed total accumulates");
    CHECK_EQ_INT(lane.bounced(), 3, "bounces accumulate too");
}

TEST(board_reads_status_through_the_base) {
    CardLane lane;

    lane.admit(7);

    const Lane &board_view = lane;
    const std::string line = board_view.status();

    CHECK_EQ_STR(line.c_str(), "card lane, 5 through, 2 re-swipes",
                 "card lane reports through a const base ref");
}

TEST(close_out_resets_every_counter) {
    CardLane lane;

    lane.admit(7);

    Lane &l = lane;

    l.reset_counts();
    CHECK_EQ_INT(lane.passed(), 0, "passed cleared at close");
    CHECK_EQ_INT(lane.bounced(), 0, "re-swipe count cleared at close");
}

TEST(bank_replaces_hardware_wholesale) {
    LaneBank bank;

    bank.install(std::make_unique<CardLane>());
    bank.at(0).admit(7);

    const std::string before = bank.at(0).status();

    CHECK_EQ_STR(before.c_str(), "card lane, 5 through, 2 re-swipes",
                 "card lane in slot 0");

    bank.replace(0, std::make_unique<Lane>());

    const std::string after = bank.at(0).status();

    CHECK_EQ_INT((long long)bank.size(), 1, "still one slot");
    CHECK_EQ_STR(after.c_str(), "lane open, 0 through",
                 "fresh plain lane reports as itself, not as the old lane");
}

TEST(bank_removes_lanes_from_service) {
    LaneBank bank;

    bank.install(std::make_unique<CardLane>());
    bank.install(std::make_unique<Lane>());
    bank.at(1).admit(2);

    bank.remove(0);

    const std::string line = bank.at(0).status();

    CHECK_EQ_INT((long long)bank.size(), 1, "one lane left");
    CHECK_EQ_STR(line.c_str(), "lane open, 2 through",
                 "the surviving lane kept its counts");
}

int main() {
    RUN(plain_lane_counts_admissions);
    RUN(card_lane_bounces_every_third_swipe);
    RUN(board_reads_status_through_the_base);
    RUN(close_out_resets_every_counter);
    RUN(bank_replaces_hardware_wholesale);
    RUN(bank_removes_lanes_from_service);
    return mt_summary();
}
