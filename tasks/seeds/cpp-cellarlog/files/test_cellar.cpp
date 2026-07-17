/* Acceptance tests for the cellar ledger and stocktake report. Build and
 * run with `make test`. Quantity rendering, ledger rows, rack back-refs
 * and stocktake lines are pinned exactly. */
#include "mintest.h"

#include "cellar.h"
#include "report.h"

#include <string>

TEST(quantities_render_in_bottle_units) {
    const std::string one = fmt_qty(1);
    const std::string none = fmt_qty(0);

    CHECK_EQ_STR(one.c_str(), "1 btl", "single bottle");
    CHECK_EQ_STR(none.c_str(), "0 btl", "empty bin still reads in btl");
}

TEST(ledger_rows_pad_labels_to_ten_columns) {
    const std::string row = cellar_row(Bin{"pinot 19", 12, nullptr});

    CHECK_EQ_STR(row.c_str(), "pinot 19   | 12 btl", "short label padded");

    const std::string wide = cellar_row(Bin{"gewurztraminer 22", 3, nullptr});

    CHECK_EQ_STR(wide.c_str(), "gewurztraminer 22 | 3 btl",
                 "long label keeps its length");
}

TEST(bins_know_their_rack) {
    Rack north{"North Wall", {}};
    const Bin racked{"riesling 21", 6, &north};
    const Bin benched{"riesling 21", 6, nullptr};

    const std::string on = bin_home_name(racked);
    const std::string off = bin_home_name(benched);

    CHECK_EQ_STR(on.c_str(), "North Wall", "racked bin names its rack");
    CHECK_EQ_STR(off.c_str(), "unracked", "bench bin is unracked");
}

TEST(stocktake_lines_total_the_rack) {
    Rack north{"North Wall",
               {Bin{"pinot 19", 12, nullptr}, Bin{"syrah 20", 5, nullptr}}};
    Rack nook{"South Nook", {}};

    const std::string line = report_line(north);
    const std::string quiet = report_line(nook);

    CHECK_EQ_STR(line.c_str(), "North Wall: 2 bins, 17 btl",
                 "two bins totalled");
    CHECK_EQ_STR(quiet.c_str(), "South Nook: 0 bins, 0 btl",
                 "empty rack still reports");
}

int main() {
    RUN(quantities_render_in_bottle_units);
    RUN(ledger_rows_pad_labels_to_ten_columns);
    RUN(bins_know_their_rack);
    RUN(stocktake_lines_total_the_rack);
    return mt_summary();
}
