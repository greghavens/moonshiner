/* vatstats.h -- batch temperature summaries for the vat telemetry board. */
#ifndef VATSTATS_H
#define VATSTATS_H

#include <cstddef>
#include <map>
#include <string>
#include <vector>

namespace vatstats {

/* Warmest reading in a batch; 0.0 for an empty batch. Works over any
 * container of doubles the loggers hand us. */
template <class C>
double peak(const C &samples) {
    if (samples.empty())
        return 0.0;
    C::const_iterator it = samples.begin();
    double best = *it;
    for (++it; it != samples.end(); ++it) {
        if (*it > best)
            best = *it;
    }
    return best;
}

/* Arithmetic mean of a batch; 0.0 for an empty batch. */
template <class C>
double mean(const C &samples) {
    if (samples.empty())
        return 0.0;
    double sum = 0.0;
    for (double v : samples)
        sum += v;
    return sum / static_cast<double>(samples.size());
}

/* One shift's readings, grouped per vat as they come off the loggers. */
class BatchLog {
  public:
    void add(const std::string &vat, double temp_c) {
        readings_[vat].push_back(temp_c);
    }

    std::size_t vats() const { return readings_.size(); }

    double vat_peak(const std::string &vat) const {
        auto it = readings_.find(vat);
        return it == readings_.end() ? 0.0 : peak(it->second);
    }

    double vat_mean(const std::string &vat) const {
        auto it = readings_.find(vat);
        return it == readings_.end() ? 0.0 : mean(it->second);
    }

  private:
    std::map<std::string, std::vector<double> readings_;
};

} // namespace vatstats

#endif /* VATSTATS_H */
