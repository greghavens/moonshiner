#ifndef BIN_H
#define BIN_H

#include "rack.h"

/* One labelled bin of bottles, with a back-pointer to the rack it
 * currently sits on (null while it waits on the sorting bench). */
struct Bin {
    std::string label;
    int bottles = 0;
    const Rack *home = nullptr;
};

/* Render a bottle count for labels and reports. */
inline std::string fmt_qty(int n) {
    return std::to_string(n) + " btl";
}

#endif /* BIN_H */
