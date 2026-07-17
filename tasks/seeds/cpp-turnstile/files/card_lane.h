#ifndef CARD_LANE_H
#define CARD_LANE_H

#include "lane.h"

/* Membership-card lane. The worn readers bounce every third swipe in a
 * party for a re-swipe, and the bounces are tracked for the maintenance
 * report. */
class CardLane : public Lane {
public:
    int admit(int people) override {
        if (people < 0)
            people = 0;

        int bounced = people / 3;

        bounced_ += bounced;
        passed_ += people - bounced;
        return people - bounced;
    }

    std::string status() override {
        return "card lane, " + std::to_string(passed_) + " through, " +
               std::to_string(bounced_) + " re-swipes";
    }

    void clear_counts() override {
        passed_ = 0;
        bounced_ = 0;
    }

    int bounced() const { return bounced_; }

private:
    int bounced_ = 0;
};

#endif /* CARD_LANE_H */
