#ifndef REPORT_H
#define REPORT_H

#include <string>

#include "rack.h"

/* Stocktake line for one rack, e.g. "North Wall: 2 bins, 17 btl". */
std::string report_line(const Rack &r);

#endif /* REPORT_H */
