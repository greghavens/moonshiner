package jobs;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Pending tug orders derived from bookings; the yard works them by arrival slot. */
public final class TugDispatcher {
    private static final class Order {
        String vesselName;
        final String berthCode;
        final int arrivalSlot;
        final int seq;

        Order(String vesselName, String berthCode, int arrivalSlot, int seq) {
            this.vesselName = vesselName;
            this.berthCode = berthCode;
            this.arrivalSlot = arrivalSlot;
            this.seq = seq;
        }
    }

    private final Map<String, Order> byBooking = new LinkedHashMap<>();
    private int nextSeq = 1;

    public void enqueue(String bookingId, String vesselName, String berthCode, int arrivalSlot) {
        byBooking.put(bookingId, new Order(vesselName, berthCode, arrivalSlot, nextSeq));
        nextSeq++;
    }

    /** A cancelled booking recalls its tug order. */
    public void withdraw(String bookingId) {
        byBooking.remove(bookingId);
    }

    /** Reflag support: pending orders follow the vessel to its new name. */
    public void retagVessel(String oldName, String newName) {
        for (Order order : byBooking.values()) {
            if (order.vesselName.equals(oldName)) {
                order.vesselName = newName;
            }
        }
    }

    /** Pending orders in working order: arrival slot, ties by dispatch sequence. */
    public List<String> pending() {
        List<Order> orders = new ArrayList<>(byBooking.values());
        orders.sort(Comparator.comparingInt((Order o) -> o.arrivalSlot)
                .thenComparingInt(o -> o.seq));
        List<String> lines = new ArrayList<>();
        for (Order order : orders) {
            lines.add("TUG " + order.berthCode + " " + order.vesselName
                    + " slot " + order.arrivalSlot);
        }
        return lines;
    }
}
