#ifndef RIDES_H
#define RIDES_H

#include "ride.h"

class Coaster : public Ride {
public:
    Coaster(int seat_count, int cycle_minutes)
        : Ride("Thunder Rail", seat_count, cycle_minutes) {}

    /* Fast-lane surcharge; the till also sells plain fares on this ride. */
    double price(int age, bool fast_lane) const {
        return Ride::price(age) + (fast_lane ? 2.0 : 0.0);
    }

    int wait_minutes(int queue_len) const override {
        int seats = capacity() - 2; /* front and back rows ride empty */
        if (seats <= 0 || queue_len <= 0)
            return 0;
        return ((queue_len + seats - 1) / seats) * cycle_min;
    }
};

class Carousel : public Ride {
public:
    Carousel(int horses, int under_repair)
        : Ride("Grand Carousel", horses, 5), resting(under_repair) {}

    int capacity() const override { return seats - resting; }

    double price(int age) const override { return age < 3 ? 0.0 : 2.0; }

private:
    int resting;
};

#endif /* RIDES_H */
