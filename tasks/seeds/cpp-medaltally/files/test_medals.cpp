/* Acceptance tests for the games medal board. Build and run with
 * `make test`. Table order, leader picks, totals and ticker lines are
 * pinned exactly. */
#include "mintest.h"

#include "medals.h"
#include "board.h"
#include "fmt.h"

#include <string>
#include <vector>

static Board<ClubRow, MedalOrder> sample_board() {
    Board<ClubRow, MedalOrder> b;

    b.add({"Milltown", 4, 2, 0});
    b.add({"Eastgate", 5, 0, 0});
    b.add({"Harbor AC", 4, 2, 1});
    b.add({"Dunmore", 4, 2, 1});
    return b;
}

TEST(table_order_counts_gold_first) {
    const MedalOrder order;
    ClubRow golds{"A", 3, 0, 0};
    ClubRow silvers{"B", 2, 9, 9};

    CHECK(order(golds, silvers), "three golds beat any pile of silver");
    CHECK(!order(silvers, golds), "and never the other way around");
}

TEST(full_ties_fall_back_to_club_name) {
    const MedalOrder order;
    ClubRow d{"Dunmore", 4, 2, 1};
    ClubRow h{"Harbor AC", 4, 2, 1};

    CHECK(order(d, h), "Dunmore alphabetically ahead on a full tie");
    CHECK(!order(h, d), "Harbor AC behind on the same tie");
}

TEST(leader_is_the_top_table_row) {
    const Board<ClubRow, MedalOrder> b = sample_board();

    CHECK_EQ_STR(b.leader().club.c_str(), "Eastgate", "five golds lead");
    CHECK_EQ_INT((long long)b.size(), 4, "all rows kept");
}

TEST(ranked_returns_the_whole_table_in_order) {
    const std::vector<ClubRow> table = sample_board().ranked();

    CHECK_EQ_INT((long long)table.size(), 4, "four table rows");
    CHECK_EQ_STR(table[0].club.c_str(), "Eastgate", "first place");
    CHECK_EQ_STR(table[1].club.c_str(), "Dunmore", "tie broken by name");
    CHECK_EQ_STR(table[2].club.c_str(), "Harbor AC", "other side of the tie");
    CHECK_EQ_STR(table[3].club.c_str(), "Milltown", "no bronze, last");
}

TEST(medal_totals_span_the_container) {
    const std::vector<ClubRow> table = sample_board().ranked();
    const std::vector<ClubRow> none;

    CHECK_EQ_INT(medal_total(table), 25, "every colour counted");
    CHECK_EQ_INT(medal_total(none), 0, "empty table totals zero");
}

TEST(ticker_lines_use_the_dash_format) {
    const TickerFmt fmt;
    const std::string line = board_line(fmt, ClubRow{"Eastgate", 5, 0, 0});

    CHECK_EQ_STR(line.c_str(), "Eastgate 5-0-0", "ticker line layout");
    CHECK_EQ_STR(board_line(fmt, ClubRow{"Harbor AC", 4, 2, 1}).c_str(),
                 "Harbor AC 4-2-1", "double-digit-free sanity row");
}

int main() {
    RUN(table_order_counts_gold_first);
    RUN(full_ties_fall_back_to_club_name);
    RUN(leader_is_the_top_table_row);
    RUN(ranked_returns_the_whole_table_in_order);
    RUN(medal_totals_span_the_container);
    RUN(ticker_lines_use_the_dash_format);
    return mt_summary();
}
