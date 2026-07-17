#include <memory>

#include "mintest.h"
#include "park.h"
#include "rides.h"

TEST(coaster_prices_both_forms) {
    Coaster c(10, 4);
    CHECK(c.price(10) == 2.5, "plain child fare");
    CHECK(c.price(30) == 4.0, "plain adult fare");
    CHECK(c.price(30, true) == 6.0, "adult fast lane");
    CHECK(c.price(10, false) == 2.5, "fast-lane form without surcharge");
}

TEST(carousel_price_dispatches_through_base) {
    Carousel k(20, 3);
    const Ride &r = k;
    CHECK(r.price(2) == 0.0, "base reference keeps toddlers free");
    CHECK(r.price(40) == 2.0, "base reference flat fare");
}

TEST(coaster_wait_keeps_two_rows_empty) {
    Coaster c(10, 4);
    CHECK_EQ_INT(c.wait_minutes(17), 12, "17 riders, 8 per cycle, 3 cycles");
    CHECK_EQ_INT(c.wait_minutes(8), 4, "exactly one cycle");
    CHECK_EQ_INT(c.wait_minutes(0), 0, "no queue, no wait");
    Coaster tiny(2, 4);
    CHECK_EQ_INT(tiny.wait_minutes(9), 0, "no boardable seats means no cycles");
}

TEST(carousel_capacity_excludes_resting_horses) {
    Carousel k(20, 3);
    CHECK_EQ_INT(k.capacity(), 17, "three horses under repair");
    CHECK_EQ_INT(k.wait_minutes(18), 10, "two five-minute turns");
    CHECK(k.price(2) == 0.0, "toddlers ride free");
    CHECK(k.price(40) == 2.0, "flat fare for everyone else");
}

TEST(park_reports_through_the_base_interface) {
    Park p;
    p.add(std::make_unique<Coaster>(10, 4));
    p.add(std::make_unique<Carousel>(20, 3));
    CHECK_EQ_INT(p.count(), 2, "two rides on the midway");
    CHECK_EQ_INT(p.total_capacity(), 27, "10 coaster seats plus 17 horses");
    CHECK_EQ_STR(p.priciest(30).c_str(), "Thunder Rail", "adults pay most on the coaster");
    CHECK_EQ_STR(p.priciest(2).c_str(), "Thunder Rail", "toddler fare comparison still finds the coaster");
}

TEST(empty_park_edge_cases) {
    Park p;
    CHECK_EQ_INT(p.count(), 0, "empty midway");
    CHECK_EQ_INT(p.total_capacity(), 0, "no capacity");
    CHECK_EQ_STR(p.priciest(30).c_str(), "", "no priciest ride");
}

int main() {
    RUN(coaster_prices_both_forms);
    RUN(carousel_price_dispatches_through_base);
    RUN(coaster_wait_keeps_two_rows_empty);
    RUN(carousel_capacity_excludes_resting_horses);
    RUN(park_reports_through_the_base_interface);
    RUN(empty_park_edge_cases);
    return mt_summary();
}
