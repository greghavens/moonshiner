#include "cellar.h"

#include "rack.h"

/* Ledger layout: labels pad out to ten columns before the divider. */
std::string format_row(const std::string &label, const std::string &right) {
    std::string padded = label;

    while (padded.size() < 10)
        padded += ' ';
    return padded + " | " + right;
}

std::string cellar_row(const Bin &b) {
    return format_row(b.label, fmt_qty(b.bottles));
}

std::string bin_home_name(const Bin &b) {
    return b.home ? b.home->name : "unracked";
}
