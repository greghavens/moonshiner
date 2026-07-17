#ifndef LANE_H
#define LANE_H

#include <string>

/* One physical entrance lane at the leisure centre. A live lane owns a
 * hardware handle, so copying one is forbidden by design — lanes move
 * through the bank as pointers, never as values. */
class Lane {
public:
    Lane() = default;
    Lane(const Lane &) = delete;
    Lane &operator=(const Lane &) = delete;
    ~Lane() = default;

    /* Admit a party of `people`; returns how many actually got through.
     * Admissions mutate real hardware counters, hence lvalue-only. */
    virtual int admit(int people) & {
        if (people < 0)
            people = 0;
        passed_ += people;
        return people;
    }

    /* Line for the entrance board, e.g. "lane open, 12 through". */
    virtual std::string status() const {
        return "lane open, " + std::to_string(passed_) + " through";
    }

    /* Zero the day counters at close of business. */
    virtual void reset_counts() { passed_ = 0; }

    int passed() const { return passed_; }

protected:
    int passed_ = 0;
};

#endif /* LANE_H */
