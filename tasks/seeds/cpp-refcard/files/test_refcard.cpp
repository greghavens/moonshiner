// test_refcard.cpp — acceptance tests for the opsq quick-reference card.
// The card wording is owned by the docs team and pinned verbatim here.
#include "mintest.h"
#include "refcard.hpp"

#include <stdexcept>
#include <string>

using namespace refcard;

TEST(card_has_three_sections)
{
    CHECK_EQ_INT(section_count(), 3, "the card has BASICS, FILTERS and KEYS");
}

TEST(section_titles_are_in_card_order)
{
    auto titles = section_titles();
    CHECK_EQ_INT(titles.size(), 3, "one title per section");
    if (titles.size() == 3) {
        CHECK_EQ_STR(titles[0].c_str(), "BASICS", "first section title");
        CHECK_EQ_STR(titles[1].c_str(), "FILTERS", "second section title");
        CHECK_EQ_STR(titles[2].c_str(), "KEYS", "third section title");
    }
}

TEST(filters_section_keeps_the_docs_wording_verbatim)
{
    const char *want =
        "FILTERS\n"
        "Use --match \"(open)\" to keep rows whose status column is exactly open.\n"
        "Use --match \"(open|held)\" when triaging a backlog sweep.\n"
        "Patterns are plain ERE; quote them so the shell leaves them alone.";
    std::string got = section(1);
    CHECK_EQ_STR(got.c_str(), want, "FILTERS section text");
}

TEST(basics_and_keys_sections_are_intact)
{
    const char *want_basics =
        "BASICS\n"
        "opsq list              show open tickets for your team\n"
        "opsq take <id>         assign a ticket to yourself\n"
        "opsq note <id> -m ...  append a note to the ticket log";
    std::string got_basics = section(0);
    CHECK_EQ_STR(got_basics.c_str(), want_basics, "BASICS section text");

    const char *want_keys =
        "KEYS\n"
        "j/k move   enter open   q quit";
    std::string got_keys = section(2);
    CHECK_EQ_STR(got_keys.c_str(), want_keys, "KEYS section text");
}

TEST(title_line_wraps_text_in_reverse_video)
{
    std::string got = title_line("FILTERS");
    CHECK_EQ_STR(got.c_str(), "\x1b[7mFILTERS\x1b[27m",
                 "reverse video switches on before the text and off after");
    CHECK_EQ_INT(got.size(), 4 + 7 + 5, "both SGR sequences are present");
    CHECK(!got.empty() && got.front() == '\x1b',
          "the row starts with an escape sequence");
}

TEST(title_line_length_tracks_its_text)
{
    CHECK_EQ_INT(title_line("KEYS").size(), 4 + 4 + 5, "KEYS title row length");
    CHECK_EQ_INT(title_line("").size(), 9, "empty title still carries both sequences");
}

TEST(out_of_range_section_throws)
{
    bool threw = false;
    try {
        (void)section(3);
    } catch (const std::out_of_range &) {
        threw = true;
    }
    CHECK(threw, "asking for a fourth section throws out_of_range");
}

int main(void)
{
    RUN(card_has_three_sections);
    RUN(section_titles_are_in_card_order);
    RUN(filters_section_keeps_the_docs_wording_verbatim);
    RUN(basics_and_keys_sections_are_intact);
    RUN(title_line_wraps_text_in_reverse_video);
    RUN(title_line_length_tracks_its_text);
    RUN(out_of_range_section_throws);
    return mt_summary();
}
