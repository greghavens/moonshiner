#ifndef RACK_H
#define RACK_H

#include <string>
#include <vector>

#include "bin.h"

/* One wall rack in the cellar and the bins racked on it. */
struct Rack {
    std::string name;
    std::vector<Bin> bins;
};

/* Render a bottle count for labels and reports. */
inline std::string fmt_qty(int n) {
    return "x" + std::to_string(n);
}

#endif /* RACK_H */
