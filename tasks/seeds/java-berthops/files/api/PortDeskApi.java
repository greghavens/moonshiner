package api;

import java.util.List;

import domain.Berth;
import domain.Booking;
import domain.BookingPolicy;
import domain.PortError;
import domain.Vessel;
import jobs.NotifyHub;
import jobs.TugDispatcher;
import store.AvailabilityCache;
import store.BerthStore;
import store.BookingStore;
import store.VesselStore;

/**
 * The berth desk: every operation an agent can perform goes through here.
 * Slots are whole hours on the shared day board; money is integer cents.
 */
public final class PortDeskApi {
    private final VesselStore vessels = new VesselStore();
    private final BerthStore berths = new BerthStore();
    private final BookingStore bookings = new BookingStore();
    private final AvailabilityCache cache = new AvailabilityCache();
    private final NotifyHub hub = new NotifyHub();
    private final TugDispatcher tugs = new TugDispatcher();
    private final BoardView board = new BoardView(berths, bookings, cache);

    // ---- register ----

    public void addBerth(String code, int maxDraftDm, int maxLengthM, long rateCentsPerHour) {
        Validation.requireName(code, "berth code");
        Validation.requirePositive(maxDraftDm, "max draft");
        Validation.requirePositive(maxLengthM, "max length");
        berths.add(new Berth(code, maxDraftDm, maxLengthM, rateCentsPerHour));
    }

    public void registerVessel(String name, int draftDm, int lengthM) {
        Validation.requireName(name, "vessel name");
        Validation.requirePositive(draftDm, "draft");
        Validation.requirePositive(lengthM, "length");
        vessels.add(new Vessel(name, draftDm, lengthM));
    }

    /** Reflag flow: the vessel keeps its bookings, tug orders and subscriptions. */
    public void renameVessel(String oldName, String newName) {
        Validation.requireName(newName, "vessel name");
        Vessel vessel = vessels.require(oldName);
        if (vessels.exists(newName)) {
            throw new PortError("DUPLICATE_VESSEL", "vessel already registered: " + newName);
        }
        vessel.rename(newName);
        vessels.rekey(oldName, vessel);
        bookings.retagVessel(oldName, newName);
        tugs.retagVessel(oldName, newName);
        hub.rekey(vessel.name(), newName);
    }

    // ---- booking ----

    public String book(String vesselName, String berthCode, int startSlot, int endSlot) {
        Vessel vessel = vessels.require(vesselName);
        Berth berth = berths.require(berthCode);
        Validation.requireWindow(startSlot, endSlot);
        BookingPolicy.checkFit(vessel, berth);
        BookingPolicy.checkWindowFree(bookings.activeForBerth(berthCode), null, startSlot, endSlot);
        String id = bookings.nextId();
        Booking booking = new Booking(id, vessel.name(), berthCode, startSlot, endSlot);
        bookings.save(booking);
        cache.invalidate(berthCode);
        tugs.enqueue(id, vessel.name(), berthCode, startSlot);
        hub.publish(vessel.name(), "BOOKED " + id + " " + berthCode + " "
                + startSlot + "-" + endSlot);
        return id;
    }

    /** Amend the departure slot of an active booking; arrival stays as dispatched. */
    public void amendEnd(String bookingId, int newEndSlot) {
        Booking booking = bookings.require(bookingId);
        requireActive(booking);
        Validation.requireWindow(booking.startSlot(), newEndSlot);
        BookingPolicy.checkWindowFree(bookings.activeForBerth(booking.berthCode()),
                bookingId, booking.startSlot(), newEndSlot);
        booking.amendEnd(newEndSlot);
        bookings.save(booking);
        hub.publish(booking.vesselName(), "AMENDED " + bookingId + " " + booking.berthCode()
                + " " + booking.startSlot() + "-" + newEndSlot);
    }

    public void cancel(String bookingId) {
        Booking booking = bookings.require(bookingId);
        requireActive(booking);
        booking.cancel();
        bookings.save(booking);
        cache.invalidate(booking.berthCode());
        tugs.withdraw(bookingId);
        hub.publish(booking.vesselName(), "CANCELLED " + bookingId);
    }

    // ---- reads ----

    /** Free hour slots on the board for a berth inside [fromSlot, toSlot). */
    public List<Integer> availability(String berthCode, int fromSlot, int toSlot) {
        return board.freeSlots(berthCode, fromSlot, toSlot);
    }

    /** Dockage quote for a booking as it stands now: hours x rate + line handling. */
    public long quoteCents(String bookingId) {
        Booking booking = bookings.require(bookingId);
        Berth berth = berths.require(booking.berthCode());
        return BookingPolicy.quoteCents(berth, booking.hours());
    }

    /** One-line desk view of a booking, exactly as the printed manifest shows it. */
    public String bookingLine(String bookingId) {
        Booking b = bookings.require(bookingId);
        return b.id() + " " + b.vesselName() + " " + b.berthCode() + " "
                + b.startSlot() + "-" + b.endSlot() + " " + b.status();
    }

    // ---- notifications / dispatch ----

    public void subscribe(String vesselName, String subscriberId) {
        Validation.requireName(subscriberId, "subscriber id");
        vessels.require(vesselName);
        hub.subscribe(vesselName, subscriberId);
    }

    public List<String> inbox(String subscriberId) {
        return hub.inbox(subscriberId);
    }

    public List<String> tugQueue() {
        return tugs.pending();
    }

    private void requireActive(Booking booking) {
        if (!booking.isActive()) {
            throw new PortError("NOT_ACTIVE", booking.id() + " is " + booking.status());
        }
    }
}
