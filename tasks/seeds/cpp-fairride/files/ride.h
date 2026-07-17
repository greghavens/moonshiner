#ifndef RIDE_H
#define RIDE_H

#include <string>
#include <utility>

/* Base class for everything on the fairground midway. Prices are per
 * rider; waits assume the queue boards in full cycles. */
class Ride {
public:
    Ride(std::string title, int seat_count, int cycle_minutes)
        : name(std::move(title)), seats(seat_count), cycle_min(cycle_minutes) {}
    ~Ride() {}

    const std::string &title() const { return name; }

    virtual int capacity() const { return seats; }

    virtual double price(int age) const { return age < 12 ? 2.5 : 4.0; }

    /* Minutes a rider at the back of the queue waits before boarding. */
    virtual int wait_minutes(int queue_len) const {
        int per_cycle = capacity();
        if (per_cycle <= 0 || queue_len <= 0)
            return 0;
        return ((queue_len + per_cycle - 1) / per_cycle) * cycle_min;
    }

protected:
    std::string name;
    int seats;
    int cycle_min;
};

#endif /* RIDE_H */
