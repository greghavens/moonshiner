#ifndef PARK_H
#define PARK_H

#include <memory>
#include <string>
#include <vector>

#include "ride.h"

/* The midway roster. Owns its rides; the season report walks them
 * through the base interface only. */
class Park {
public:
    void add(std::unique_ptr<Ride> ride) { rides.push_back(std::move(ride)); }

    std::size_t count() const { return rides.size(); }

    int total_capacity() const {
        int total = 0;
        for (const auto &r : rides)
            total += r->capacity();
        return total;
    }

    /* Title of the most expensive ride for a rider of this age;
     * first added wins ties. Empty string for an empty park. */
    std::string priciest(int age) const {
        std::string best;
        double best_price = -1.0;
        for (const auto &r : rides) {
            double p = r->price(age);
            if (p > best_price) {
                best_price = p;
                best = r->title();
            }
        }
        return best;
    }

private:
    std::vector<std::unique_ptr<Ride>> rides;
};

#endif /* PARK_H */
