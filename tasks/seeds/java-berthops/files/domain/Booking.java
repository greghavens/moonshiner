package domain;

import java.util.ArrayList;
import java.util.List;

/** One berth occupation window. Slots are whole hours on the shared day board. */
public final class Booking {
    public static final String ACTIVE = "ACTIVE";
    public static final String CANCELLED = "CANCELLED";

    private final String id;
    private String vesselName;
    private final String berthCode;
    private final int startSlot;
    private int endSlot;
    private String status = ACTIVE;

    public Booking(String id, String vesselName, String berthCode, int startSlot, int endSlot) {
        this.id = id;
        this.vesselName = vesselName;
        this.berthCode = berthCode;
        this.startSlot = startSlot;
        this.endSlot = endSlot;
    }

    public String id() {
        return id;
    }

    public String vesselName() {
        return vesselName;
    }

    public String berthCode() {
        return berthCode;
    }

    public int startSlot() {
        return startSlot;
    }

    public int endSlot() {
        return endSlot;
    }

    public String status() {
        return status;
    }

    public boolean isActive() {
        return ACTIVE.equals(status);
    }

    public int hours() {
        return endSlot - startSlot;
    }

    /** Arrival never moves once a tug is dispatched; only departure can be amended. */
    public void amendEnd(int newEndSlot) {
        this.endSlot = newEndSlot;
    }

    public void cancel() {
        this.status = CANCELLED;
    }

    /** Reflag support: bookings follow the vessel to its new name. */
    public void retagVessel(String newName) {
        this.vesselName = newName;
    }

    public boolean overlaps(int fromSlot, int toSlot) {
        return startSlot < toSlot && fromSlot < endSlot;
    }

    public List<Integer> occupiedSlots() {
        List<Integer> slots = new ArrayList<>();
        for (int s = startSlot; s < endSlot; s++) {
            slots.add(s);
        }
        return slots;
    }
}
