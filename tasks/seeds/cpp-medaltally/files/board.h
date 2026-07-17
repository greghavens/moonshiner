#ifndef BOARD_H
#define BOARD_H

#include <algorithm>
#include <cstddef>
#include <vector>

namespace detail {

/* Index of the row the comparator ranks first. Rows must be non-empty. */
template <typename T, typename Cmp>
std::size_t lead_index(const std::vector<T> &rows, const Cmp &cmp) {
    std::size_t best = 0;

    for (std::size_t i = 1; i < rows.size(); ++i)
        if (cmp(rows[i], rows[best]))
            best = i;
    return best;
}

} // namespace detail

/* A standings board: rows plus the ordering policy for the table. */
template <typename T, typename Cmp>
class Board {
public:
    void add(T row) { rows_.push_back(std::move(row)); }

    std::size_t size() const { return rows_.size(); }

    /* The row currently on top of the table. */
    const T &leader() const { return rows_[detail::lead_index(rows_, cmp_)]; }

    /* All rows in table order. */
    std::vector<T> ranked() const {
        std::vector<T> out = rows_;

        std::stable_sort(out.begin(), out.end(), cmp_);
        return out;
    }

private:
    std::vector<T> rows_;
    Cmp cmp_;
};

/* Total medals of every colour across any row container. */
template <typename Rows>
int medal_total(const Rows &rows) {
    int total = 0;

    for (Rows::const_iterator it = rows.begin(); it != rows.end(); ++it)
        total += it->gold + it->silver + it->bronze;
    return total;
}

#endif /* BOARD_H */
