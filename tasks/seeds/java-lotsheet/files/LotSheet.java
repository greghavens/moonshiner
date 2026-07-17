import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;

/**
 * Sale-day lot sheet for the auction house: every consigned lot, plus the
 * order in which lots cross the block. Ring 1 sells first; within a ring
 * the pricier reserves lead so the crowd is still warm.
 */
public class LotSheet {
    private final List<Lot> lots = new ArrayList<>();

    /** Block order: earlier ring first, higher reserve first, catalog number breaks ties. */
    private final Comparator<Lot> saleOrder = new Comparator<Lot>() {
        @Override
        public int compare(Lot a, Lot b) {
            if (a.ring() != b.ring()) {
                return Integer.compare(a.ring(), b.ring());
            if (a.reserve() != b.reserve()) {
                return Integer.compare(b.reserve(), a.reserve());
            }
            return a.number().compareTo(b.number());
        }
    };

    public void add(Lot lot) {
        for (Lot existing : lots) {
            if (existing.number().equals(lot.number())) {
                throw new IllegalArgumentException("duplicate lot number: " + lot.number());
            }
        }
        lots.add(lot);
    }

    public int count() {
        return lots.size();
    }

    /** All lots in block order. The sheet itself keeps consignment order. */
    public List<Lot> ordered() {
        List<Lot> copy = new ArrayList<>(lots);
        copy.sort(saleOrder);
        return copy;
    }

    /** Lots selling in one ring, in block order. */
    public List<Lot> ring(int ring) {
        List<Lot> out = new ArrayList<>();
        for (Lot lot : ordered()) {
            if (lot.ring() == ring) {
                out.add(lot);
            }
        }
        return out;
    }

    /** Sum of reserves for a ring, for the auctioneer's morning summary. */
    public int reserveTotal(int ring) {
        int total = 0;
        for (Lot lot : lots) {
            if (lot.ring() == ring) {
                total += lot.reserve();
            }
        }
        return total;
    }

    /** The lot that opens the sale, if anything is consigned at all. */
    public Optional<Lot> opener() {
        List<Lot> order = ordered();
        return order.isEmpty() ? Optional.empty() : Optional.of(order.get(0));
    }
}
