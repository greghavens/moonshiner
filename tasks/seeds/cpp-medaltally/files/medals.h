#ifndef MEDALS_H
#define MEDALS_H

#include <string>

/* One club's line on the games medal table. */
struct ClubRow {
    std::string club;
    int gold = 0;
    int silver = 0;
    int bronze = 0;
};

/* Table order: more golds first, then silvers, then bronzes, and
 * alphabetical club name to settle full ties. Returns true when a
 * ranks strictly ahead of b. */
struct MedalOrder {
    bool operator()(const ClubRow &a, const ClubRow &b) {
        if (a.gold != b.gold)
            return a.gold > b.gold;
        if (a.silver != b.silver)
            return a.silver > b.silver;
        if (a.bronze != b.bronze)
            return a.bronze > b.bronze;
        return a.club < b.club;
    }
};

#endif /* MEDALS_H */
