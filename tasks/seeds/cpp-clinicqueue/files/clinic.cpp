#include "clinic.hpp"

#include <algorithm>
#include <utility>

void WaitList::arrive(Visit v) {
    by_ticket_[v.ticket] = queue_.size();
    queue_.push_back(std::move(v));
}

std::size_t WaitList::size() const {
    return queue_.size();
}

const Visit *WaitList::lookup(const std::string &ticket) const {
    auto it = by_ticket_.find(ticket);
    if (it == by_ticket_.end() || it->second >= queue_.size())
        return nullptr;
    return &queue_[it->second];
}

std::vector<std::string> WaitList::order() const {
    std::vector<std::string> tickets;
    tickets.reserve(queue_.size());
    for (const Visit &v : queue_)
        tickets.push_back(v.ticket);
    return tickets;
}

int WaitList::sweep_holds(int now) {
    int dropped = 0;
    for (std::size_t i = 0; i < queue_.size(); i++) {
        if (queue_[i].hold_until <= now) {
            by_ticket_.erase(queue_[i].ticket);
            queue_.erase(queue_.begin() + static_cast<std::ptrdiff_t>(i));
            dropped++;
        }
    }
    dropped_total_ += dropped;
    return dropped;
}

int WaitList::dropped_total() const {
    return dropped_total_;
}

std::vector<std::string> WaitList::next_up(std::size_t n) const {
    std::vector<const Visit *> by_urgency;
    by_urgency.reserve(queue_.size());
    for (const Visit &v : queue_)
        by_urgency.push_back(&v);
    std::stable_sort(by_urgency.begin(), by_urgency.end(),
                     [](const Visit *a, const Visit *b) {
                         return a->priority < b->priority;
                     });
    if (n < by_urgency.size())
        by_urgency.resize(n);
    std::vector<std::string> tickets;
    tickets.reserve(by_urgency.size());
    for (const Visit *v : by_urgency)
        tickets.push_back(v->ticket);
    return tickets;
}
