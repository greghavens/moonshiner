// refcard.cpp — the quick-reference card behind `opsq help`.
//
// The card text is owned by the docs team; code only slices it up and
// decorates titles for the terminal. Sections are separated by a line
// holding exactly "%%", and the first line of a section is its title.
#include "refcard.hpp"

#include <stdexcept>

namespace refcard {

namespace {

const char *CARD = R"(BASICS
opsq list              show open tickets for your team
opsq take <id>         assign a ticket to yourself
opsq note <id> -m ...  append a note to the ticket log
%%
FILTERS
Use --match "(open)" to keep rows whose status column is exactly open.
Use --match "(open|held)" when triaging a backlog sweep.
Patterns are plain ERE; quote them so the shell leaves them alone.
%%
KEYS
j/k move   enter open   q quit
)";

// Title rows render in reverse video (SGR 7 to switch on, 27 to switch off).
const char *TITLE_ON = "\0x1b[7m";
const char *TITLE_OFF = "\x1b[27m";

std::vector<std::string> split_sections()
{
    std::vector<std::string> sections;
    std::string current;
    bool current_started = false;

    const std::string card(CARD);
    std::size_t pos = 0;
    while (pos <= card.size()) {
        std::size_t nl = card.find('\n', pos);
        if (nl == std::string::npos)
            nl = card.size();
        std::string line = card.substr(pos, nl - pos);
        pos = nl + 1;

        if (line == "%%") {
            sections.push_back(current);
            current.clear();
            current_started = false;
            continue;
        }
        if (pos > card.size() && line.empty())
            break; // card text ends with a newline; no trailing blank section
        if (current_started)
            current += '\n';
        current += line;
        current_started = true;
    }
    if (current_started)
        sections.push_back(current);
    return sections;
}

const std::vector<std::string> &sections_cache()
{
    static const std::vector<std::string> cached = split_sections();
    return cached;
}

} // namespace

std::size_t section_count()
{
    return sections_cache().size();
}

std::string section(std::size_t idx)
{
    const auto &all = sections_cache();
    if (idx >= all.size())
        throw std::out_of_range("refcard: no such section");
    return all[idx];
}

std::vector<std::string> section_titles()
{
    std::vector<std::string> titles;
    for (const auto &sec : sections_cache()) {
        std::size_t nl = sec.find('\n');
        titles.push_back(nl == std::string::npos ? sec : sec.substr(0, nl));
    }
    return titles;
}

std::string title_line(const std::string &text)
{
    return std::string(TITLE_ON) + text + std::string(TITLE_OFF);
}

} // namespace refcard
