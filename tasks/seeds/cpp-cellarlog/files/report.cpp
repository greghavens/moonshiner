#include "report.h"

/* Stocktake layout: the rack name owns the line, details after a colon. */
std::string format_row(const std::string &name, const std::string &detail) {
    return name + ": " + detail;
}

std::string report_line(const Rack &r) {
    int total = 0;

    for (const Bin &b : r.bins)
        total += b.bottles;
    return format_row(r.name, std::to_string(r.bins.size()) + " bins, " +
                                  fmt_qty(total));
}
