#ifndef REFCARD_HPP
#define REFCARD_HPP

#include <cstddef>
#include <string>
#include <vector>

namespace refcard {

// Number of sections on the quick-reference card.
std::size_t section_count();

// Full text of one section (0-based), without the %% separator lines.
// Throws std::out_of_range for an index past the last section.
std::string section(std::size_t idx);

// First line of every section, in card order.
std::vector<std::string> section_titles();

// A section title decorated for the terminal (reverse video on/off).
std::string title_line(const std::string &text);

} // namespace refcard

#endif // REFCARD_HPP
