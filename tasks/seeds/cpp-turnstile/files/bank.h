#ifndef BANK_H
#define BANK_H

#include "lane.h"

#include <cstddef>
#include <memory>
#include <utility>
#include <vector>

/* Power down and free a lane that has been pulled from the bank. */
inline void decommission(Lane *lane) {
    /* the hardware power-down handshake would go here */
    delete lane;
}

/* The row of lanes at the entrance. The bank owns its lanes. */
class LaneBank {
public:
    void install(std::unique_ptr<Lane> lane) {
        lanes_.push_back(std::move(lane));
    }

    /* Swap freshly configured hardware into slot i; the old lane is
     * decommissioned and torn down. */
    void replace(std::size_t i, std::unique_ptr<Lane> fresh) {
        *lanes_[i] = *fresh;
    }

    /* Pull a lane out of service entirely. */
    void remove(std::size_t i) {
        decommission(lanes_[i].release());
        lanes_.erase(lanes_.begin() + (long)i);
    }

    Lane &at(std::size_t i) { return *lanes_[i]; }
    const Lane &at(std::size_t i) const { return *lanes_[i]; }

    std::size_t size() const { return lanes_.size(); }

private:
    std::vector<std::unique_ptr<Lane>> lanes_;
};

#endif /* BANK_H */
