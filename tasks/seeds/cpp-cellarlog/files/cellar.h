#ifndef CELLAR_H
#define CELLAR_H

#include <string>

#include "bin.h"

/* Ledger row for one bin, e.g. "pinot 19   | 12 btl". */
std::string cellar_row(const Bin &b);

/* Name of the rack a bin lives on, or "unracked" from the bench. */
std::string bin_home_name(const Bin &b);

#endif /* CELLAR_H */
