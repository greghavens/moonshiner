"""Shelf-bin bookkeeping: each category flows into numbered bins."""


class BinIndex:
    """Tracks how full each shelf bin is.

    bins: list of (bin_id, capacity) tuples, in shelf order. A placement
    goes into the first bin that still has room for the whole quantity;
    a quantity nothing can hold raises ValueError.
    """

    def __init__(self, bins):
        self._order = [bin_id for bin_id, _ in bins]
        self._capacity = dict(bins)
        self._load = {bin_id: 0.0 for bin_id in self._order}
        self._contents = {bin_id: [] for bin_id in self._order}

    def place(self, category, qty):
        for bin_id in self._order:
            if self._load[bin_id] + qty <= self._capacity[bin_id]:
                self._load[bin_id] += qty
                self._contents[bin_id].append(category)
                return bin_id
        raise ValueError(f"no bin can hold {qty:g} of {category}")

    def load_of(self, bin_id):
        return self._load[bin_id]

    def manifest(self):
        """(bin_id, load, sorted unique categories) per bin, shelf order."""
        return [
            (b, self._load[b], sorted(set(self._contents[b])))
            for b in self._order
        ]
