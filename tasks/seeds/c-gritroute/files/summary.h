#ifndef SUMMARY_H
#define SUMMARY_H

#include <stddef.h>

struct leg;

/* One-line depot summary for a route, e.g.
 *   legs=3 km=25 plan=348kg reserve=652kg worst-rate=28  */
void grit_summary_line(char *dst, size_t cap, const struct leg *legs, int n);

#endif /* SUMMARY_H */
