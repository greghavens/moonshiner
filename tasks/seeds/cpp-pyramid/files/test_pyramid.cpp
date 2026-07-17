/* Acceptance suite for the pyramid solitaire engine.
 * Both deals and every pinned string/count were validated against an
 * independent implementation of the stated rules. */
#include <string>

#include "mintest.h"
#include "pyramid.h"

/* Winnable deal: the pyramid clears row by row, with four waste pairings
 * in the middle. */
static const char *WINNABLE =
    "KC 5C 4C KD 8C 9C 3C TC 3D TD KH 2C JC 2D JD AC QC AD QD AH QH "
    "KS 6C 7C 6D 7D 6H 7H 5D 4D 8D 9D 6S 7S AS QS 2H 2S JH JS 3H 3S "
    "TH TS 8H 8S 9H 9S 5H 5S 4H 4S";

/* Nothing on the bottom row ever pairs; the game can only cycle the stock
 * until the recycles run out. */
static const char *DEAD_END =
    "KC KD KH KS QC QD QH QS JC JD JH JS TC TD TH TS 9C 9D 9H 9S 8C "
    "AC 2C 3C 4C 5C 6C AD 8D 8H 8S 7C 7D 7H 7S 2D 2H 2S 3D 3H 3S 4D "
    "4H 4S 5D 5H 5S 6D 6H AH AS 6S";

TEST(load_validation) {
    Pyramid g;
    CHECK(pyr_load(&g, WINNABLE), "well-formed 52-card deal loads");
    CHECK(!pyr_load(&g, "KC 5C 4C"), "short deal rejected");
    CHECK(!pyr_load(&g, ""), "empty deal rejected");
    std::string dup(WINNABLE);
    dup.replace(0, 2, "5C");   /* first card duplicates the second */
    CHECK(!pyr_load(&g, dup), "duplicate card rejected");
    std::string junk(WINNABLE);
    junk.replace(0, 2, "XX");
    CHECK(!pyr_load(&g, junk), "unknown card token rejected");
}

TEST(initial_layout_and_render) {
    Pyramid g;
    CHECK(pyr_load(&g, WINNABLE), "deal loads");
    CHECK_EQ_INT(pyr_stock_size(&g), 24, "24 cards go to the stock");
    CHECK_EQ_INT(pyr_waste_size(&g), 0, "waste starts empty");
    std::string top = pyr_waste_top(&g);
    CHECK_EQ_STR(top.c_str(), "", "no waste top before the first draw");
    CHECK_EQ_INT(pyr_state(&g), 0, "fresh deal is in play");
    CHECK(pyr_exposed(&g, 6, 0), "bottom row starts exposed");
    CHECK(pyr_exposed(&g, 6, 6), "all of it");
    CHECK(!pyr_exposed(&g, 5, 0), "covered rows do not");
    CHECK(!pyr_exposed(&g, 0, 0), "apex included");
    CHECK(!pyr_exposed(&g, 7, 0), "out-of-range spot is not exposed");
    std::string r = pyr_render(&g);
    CHECK_EQ_STR(r.c_str(),
                 "            KC\n"
                 "          5C  4C\n"
                 "        KD  8C  9C\n"
                 "      3C  TC  3D  TD\n"
                 "    KH  2C  JC  2D  JD\n"
                 "  AC  QC  AD  QD  AH  QH\n"
                 "KS  6C  7C  6D  7D  6H  7H\n",
                 "initial render matches card for card");
}

TEST(removal_legality) {
    Pyramid g;
    CHECK(pyr_load(&g, WINNABLE), "deal loads");
    CHECK(!pyr_remove(&g, "P61", "P63"), "6+6 is not thirteen");
    CHECK(!pyr_remove(&g, "P50", "P51"), "covered cards cannot pair");
    CHECK(!pyr_remove(&g, "P60", "P61"), "a king cannot join a pair");
    CHECK(!pyr_remove(&g, "P61", "P61"), "a card cannot pair with itself");
    CHECK(!pyr_remove(&g, "W", "W"), "the waste top cannot pair with itself");
    CHECK(!pyr_remove(&g, "W", "P62"), "empty waste offers nothing");
    CHECK(!pyr_remove_king(&g, "P61"), "remove-king wants a king");
    CHECK(!pyr_remove_king(&g, "P00"), "a covered king does not come off");
    CHECK(pyr_remove(&g, "P61", "P62"), "exposed 6+7 comes off");
    CHECK(!pyr_exposed(&g, 6, 1), "removed spots are gone");
    CHECK(pyr_remove_king(&g, "P60"), "exposed king comes off alone");
    CHECK(pyr_exposed(&g, 5, 0), "both covers gone exposes the card above");
    CHECK(pyr_exposed(&g, 5, 1), "on both sides");
    CHECK(!pyr_exposed(&g, 5, 2), "one cover still in place is not enough");
}

TEST(win_the_scripted_deal) {
    Pyramid g;
    CHECK(pyr_load(&g, WINNABLE), "deal loads");
    CHECK(pyr_remove_king(&g, "P60"), "king off the bottom row");
    CHECK(pyr_remove(&g, "P61", "P62"), "6+7");
    CHECK(pyr_remove(&g, "P63", "P64"), "6+7 again");
    CHECK(pyr_remove(&g, "P65", "P66"), "and again");
    CHECK(pyr_remove(&g, "P50", "P51"), "A+Q");
    CHECK(pyr_remove(&g, "P52", "P53"), "A+Q");
    CHECK(pyr_remove(&g, "P54", "P55"), "A+Q");
    CHECK(pyr_remove_king(&g, "P40"), "king off row 4");
    CHECK(pyr_remove(&g, "P41", "P42"), "2+J");
    CHECK(pyr_remove(&g, "P43", "P44"), "2+J");
    CHECK(pyr_remove(&g, "P30", "P31"), "3+T");
    CHECK(pyr_remove(&g, "P32", "P33"), "3+T");
    std::string r = pyr_render(&g);
    CHECK_EQ_STR(r.c_str(),
                 "            KC\n"
                 "          5C  4C\n"
                 "        KD  8C  9C\n"
                 "      --  --  --  --\n"
                 "    --  --  --  --  --\n"
                 "  --  --  --  --  --  --\n"
                 "--  --  --  --  --  --  --\n",
                 "cleared spots render as --");
    CHECK_EQ_INT(pyr_state(&g), 0, "still in play");
    CHECK(pyr_remove_king(&g, "P20"), "king off row 2");
    CHECK(!pyr_remove(&g, "W", "P21"), "no waste card yet");
    CHECK(pyr_draw(&g), "draw the first stock card");
    std::string top = pyr_waste_top(&g);
    CHECK_EQ_STR(top.c_str(), "5D", "the 5 of diamonds surfaces");
    CHECK(pyr_remove(&g, "W", "P21"), "waste 5 takes the exposed 8");
    CHECK_EQ_INT(pyr_waste_size(&g), 0, "waste is empty again");
    CHECK(pyr_draw(&g), "draw");
    CHECK(pyr_remove(&g, "W", "P22"), "waste 4 takes the 9");
    CHECK(pyr_draw(&g), "draw");
    CHECK(pyr_remove(&g, "W", "P10"), "waste 8 takes the 5");
    CHECK(pyr_draw(&g), "draw");
    CHECK(pyr_remove(&g, "W", "P11"), "waste 9 takes the 4");
    CHECK(pyr_exposed(&g, 0, 0), "the apex is finally exposed");
    CHECK_EQ_INT(pyr_state(&g), 0, "one king to go");
    CHECK(pyr_remove_king(&g, "P00"), "apex king comes off");
    CHECK_EQ_INT(pyr_state(&g), 1, "empty pyramid means the game is won");
    CHECK_EQ_INT(pyr_stock_size(&g), 20, "leftover stock does not matter");
}

TEST(stock_waste_and_recycle_cycle) {
    Pyramid g;
    CHECK(pyr_load(&g, DEAD_END), "deal loads");
    CHECK(!pyr_recycle(&g), "recycle needs an empty stock");
    CHECK(pyr_draw(&g), "draw 1");
    std::string t1 = pyr_waste_top(&g);
    CHECK_EQ_STR(t1.c_str(), "8D", "stock comes off in deal order");
    CHECK(pyr_draw(&g), "draw 2");
    CHECK(pyr_draw(&g), "draw 3");
    std::string t3 = pyr_waste_top(&g);
    CHECK_EQ_STR(t3.c_str(), "8S", "third card up");
    CHECK_EQ_INT(pyr_stock_size(&g), 21, "stock shrinks as the waste grows");
    CHECK_EQ_INT(pyr_waste_size(&g), 3, "three in the waste");
    while (pyr_stock_size(&g) > 0) pyr_draw(&g);
    std::string tEnd = pyr_waste_top(&g);
    CHECK_EQ_STR(tEnd.c_str(), "6S", "last stock card tops the waste");
    CHECK_EQ_INT(pyr_state(&g), 0, "a recycle is still available: in play");
    CHECK(pyr_recycle(&g), "first recycle");
    CHECK_EQ_INT(pyr_stock_size(&g), 24, "everything back in the stock");
    CHECK_EQ_INT(pyr_waste_size(&g), 0, "waste empty after recycle");
    CHECK(pyr_draw(&g), "draw again");
    std::string t1b = pyr_waste_top(&g);
    CHECK_EQ_STR(t1b.c_str(), "8D", "recycling preserves the draw order");
    while (pyr_stock_size(&g) > 0) pyr_draw(&g);
    CHECK(pyr_recycle(&g), "second recycle");
    while (pyr_stock_size(&g) > 0) pyr_draw(&g);
    CHECK(!pyr_recycle(&g), "the third recycle is not allowed");
    CHECK(!pyr_draw(&g), "and the stock is dry");
    CHECK_EQ_INT(pyr_state(&g), 2, "no removal, no draw, no recycle: stuck");
}

int main(void) {
    RUN(load_validation);
    RUN(initial_layout_and_render);
    RUN(removal_legality);
    RUN(win_the_scripted_deal);
    RUN(stock_waste_and_recycle_cycle);
    return mt_summary();
}
