#pragma once

#include <cstddef>
#include <map>
#include <string>
#include <vector>

/* Walk-in waitlist for the front desk. Visits queue in arrival order; the
 * desk can call any visit up by ticket, and reception periodically clears
 * visits whose owner-hold has lapsed. */

struct Visit {
    std::string ticket;   // "T-104"
    std::string patient;  // "Ziggy (corgi)"
    int priority;         // 1 = urgent ... 5 = routine
    int hold_until;       // minutes since opening; owner must be back by then
};

class WaitList {
public:
    /* New arrival at the back of the queue. Tickets are unique. */
    void arrive(Visit v);

    std::size_t size() const;

    /* Desk lookup by ticket. Returns nullptr for tickets not on the board. */
    const Visit *lookup(const std::string &ticket) const;

    /* Tickets in queue (arrival) order. */
    std::vector<std::string> order() const;

    /* Remove every visit whose hold_until is at or before `now`.
     * Returns how many visits were removed by this call. */
    int sweep_holds(int now);

    /* Running total of removals across all sweeps. */
    int dropped_total() const;

    /* The next `n` tickets to call: most urgent priority first, earlier
     * arrival breaking ties. */
    std::vector<std::string> next_up(std::size_t n) const;

private:
    std::vector<Visit> queue_;                      // arrival order
    std::map<std::string, std::size_t> by_ticket_;  // ticket -> queue_ index
    int dropped_total_ = 0;
};
