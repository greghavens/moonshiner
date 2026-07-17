#ifndef FMT_H
#define FMT_H

#include <string>

/* Renders board rows for the results ticker. */
struct TickerFmt {
    template <typename T>
    std::string render(const T &row) const {
        return row.club + " " + std::to_string(row.gold) + "-" +
               std::to_string(row.silver) + "-" + std::to_string(row.bronze);
    }
};

/* One ticker line for a row, using whichever formatter the caller wires in. */
template <typename F, typename T>
std::string board_line(const F &fmt, const T &row) {
    return fmt.render<T>(row);
}

#endif /* FMT_H */
