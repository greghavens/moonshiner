import java.util.List;
import java.util.Locale;

import api.PortDeskApi;
import domain.PortError;

/**
 * Acceptance contract for the berth desk: board availability, booking windows,
 * tariff quotes, tug dispatch and vessel-subscription notices. All money is
 * integer cents, all times are whole hour slots on the shared day board, and
 * every expected value below was recomputed by hand from the tariff sheet
 * (rate 1850 cents/hour on NQ1, flat line handling 3000 cents).
 *
 * Run: java TestMain.java
 */
public class TestMain {

    interface Body {
        void run() throws Exception;
    }

    static int passed = 0;
    static int failed = 0;

    static void test(String name, Body body) {
        try {
            body.run();
            passed++;
            System.out.println("PASS " + name);
        } catch (Throwable t) {
            failed++;
            System.out.println("FAIL " + name + ": " + t);
        }
    }

    static void eq(Object actual, Object expected, String what) {
        if (actual == null ? expected != null : !actual.equals(expected)) {
            throw new AssertionError(what + ": expected <" + expected + "> got <" + actual + ">");
        }
    }

    static void throwsCode(String code, Body body, String what) {
        try {
            body.run();
        } catch (PortError e) {
            if (e.code().equals(code)) {
                return;
            }
            throw new AssertionError(what + ": expected code " + code + " got " + e.code()
                    + " (" + e.getMessage() + ")");
        } catch (Throwable t) {
            throw new AssertionError(what + ": expected PortError " + code + " got " + t);
        }
        throw new AssertionError(what + ": expected PortError " + code + ", nothing thrown");
    }

    /** A desk with berth NQ1 (draft 85 dm, length 190 m, 1850 cents/h) and one vessel. */
    static PortDeskApi newDesk() {
        PortDeskApi desk = new PortDeskApi();
        desk.addBerth("NQ1", 85, 190, 1850);
        desk.registerVessel("Coppermine", 78, 142);
        return desk;
    }

    public static void main(String[] args) {
        Locale.setDefault(Locale.ROOT);

        test("board_starts_fully_open", () -> {
            PortDeskApi desk = newDesk();
            eq(desk.availability("NQ1", 8, 18),
                    List.of(8, 9, 10, 11, 12, 13, 14, 15, 16, 17), "empty board 8..18");
        });

        test("booking_occupies_window_and_quotes", () -> {
            PortDeskApi desk = newDesk();
            String id = desk.book("Coppermine", "NQ1", 10, 14);
            eq(id, "BK-1", "first booking id");
            eq(desk.availability("NQ1", 8, 18), List.of(8, 9, 14, 15, 16, 17), "board after booking");
            // 4 hours x 1850 = 7400; + 3000 line handling = 10400
            eq(desk.quoteCents("BK-1"), 10400L, "quote for 10-14");
            eq(desk.bookingLine("BK-1"), "BK-1 Coppermine NQ1 10-14 ACTIVE", "manifest line");
            eq(desk.tugQueue(), List.of("TUG NQ1 Coppermine slot 10"), "tug order");
        });

        test("overlap_rejected_adjacent_allowed", () -> {
            PortDeskApi desk = newDesk();
            desk.registerVessel("Gritstone", 64, 118);
            desk.book("Coppermine", "NQ1", 10, 14);
            throwsCode("WINDOW_CONFLICT",
                    () -> desk.book("Gritstone", "NQ1", 12, 15), "overlapping window");
            eq(desk.book("Gritstone", "NQ1", 14, 16), "BK-2", "back-to-back window");
            eq(desk.availability("NQ1", 8, 18), List.of(8, 9, 16, 17), "board with both stays");
        });

        test("fit_and_window_rules", () -> {
            PortDeskApi desk = newDesk();
            desk.registerVessel("Deepholm", 92, 150);
            desk.registerVessel("Longreach", 70, 210);
            throwsCode("VESSEL_TOO_DEEP", () -> desk.book("Deepholm", "NQ1", 8, 10), "draft over limit");
            throwsCode("VESSEL_TOO_LONG", () -> desk.book("Longreach", "NQ1", 8, 10), "length over limit");
            throwsCode("BAD_WINDOW", () -> desk.book("Coppermine", "NQ1", 14, 14), "empty window");
            throwsCode("BAD_WINDOW", () -> desk.book("Coppermine", "NQ1", -2, 3), "negative start");
        });

        test("cancel_reopens_board_and_recalls_tug", () -> {
            PortDeskApi desk = newDesk();
            desk.book("Coppermine", "NQ1", 10, 14);
            eq(desk.availability("NQ1", 8, 18), List.of(8, 9, 14, 15, 16, 17), "board while held");
            desk.cancel("BK-1");
            eq(desk.availability("NQ1", 8, 18),
                    List.of(8, 9, 10, 11, 12, 13, 14, 15, 16, 17), "board after cancel");
            eq(desk.tugQueue(), List.of(), "tug order recalled");
            throwsCode("NOT_ACTIVE", () -> desk.cancel("BK-1"), "double cancel");
            throwsCode("NOT_ACTIVE", () -> desk.amendEnd("BK-1", 16), "amend cancelled stay");
        });

        test("amend_departure_updates_quote", () -> {
            PortDeskApi desk = newDesk();
            desk.book("Coppermine", "NQ1", 10, 14);
            // 4 x 1850 + 3000 = 10400
            eq(desk.quoteCents("BK-1"), 10400L, "quote before amendment");
            desk.amendEnd("BK-1", 17);
            // 7 x 1850 = 12950; + 3000 = 15950
            eq(desk.quoteCents("BK-1"), 15950L, "quote after extending to 17");
            eq(desk.bookingLine("BK-1"), "BK-1 Coppermine NQ1 10-17 ACTIVE", "manifest after amend");
        });

        test("amend_departure_updates_board", () -> {
            PortDeskApi desk = newDesk();
            desk.book("Coppermine", "NQ1", 10, 14);
            eq(desk.availability("NQ1", 8, 18), List.of(8, 9, 14, 15, 16, 17), "board before amend");
            desk.amendEnd("BK-1", 17);
            eq(desk.availability("NQ1", 8, 18), List.of(8, 9, 17), "board after extending to 17");
        });

        test("amend_shorter_stay_reopens_hours", () -> {
            PortDeskApi desk = newDesk();
            desk.book("Coppermine", "NQ1", 10, 16);
            eq(desk.availability("NQ1", 8, 18), List.of(8, 9, 16, 17), "board before amend");
            desk.amendEnd("BK-1", 12);
            eq(desk.availability("NQ1", 8, 18),
                    List.of(8, 9, 12, 13, 14, 15, 16, 17), "board after shortening to 12");
        });

        test("desk_refuses_conflict_after_amendment", () -> {
            PortDeskApi desk = newDesk();
            desk.registerVessel("Gritstone", 64, 118);
            desk.book("Coppermine", "NQ1", 10, 14);
            eq(desk.availability("NQ1", 8, 18), List.of(8, 9, 14, 15, 16, 17), "board while held");
            desk.amendEnd("BK-1", 17);
            throwsCode("WINDOW_CONFLICT",
                    () -> desk.book("Gritstone", "NQ1", 14, 16), "window taken by the extension");
        });

        test("subscription_receives_booking_events", () -> {
            PortDeskApi desk = newDesk();
            desk.subscribe("Coppermine", "agent-mia");
            desk.book("Coppermine", "NQ1", 10, 14);
            desk.amendEnd("BK-1", 15);
            desk.cancel("BK-1");
            eq(desk.inbox("agent-mia"),
                    List.of("BOOKED BK-1 NQ1 10-14", "AMENDED BK-1 NQ1 10-15", "CANCELLED BK-1"),
                    "event trail");
        });

        test("subscribe_requires_known_vessel", () -> {
            PortDeskApi desk = newDesk();
            throwsCode("UNKNOWN_VESSEL", () -> desk.subscribe("Ghostship", "agent-mia"),
                    "subscribe to unregistered vessel");
            eq(desk.inbox("agent-mia"), List.of(), "inbox stays empty");
        });

        test("reflag_carries_bookings_and_tugs", () -> {
            PortDeskApi desk = newDesk();
            desk.book("Coppermine", "NQ1", 10, 14);
            desk.renameVessel("Coppermine", "Novara Star");
            eq(desk.bookingLine("BK-1"), "BK-1 Novara Star NQ1 10-14 ACTIVE", "manifest retagged");
            eq(desk.tugQueue(), List.of("TUG NQ1 Novara Star slot 10"), "tug order retagged");
            eq(desk.book("Novara Star", "NQ1", 14, 16), "BK-2", "new name can book");
            throwsCode("UNKNOWN_VESSEL", () -> desk.book("Coppermine", "NQ1", 16, 17),
                    "old name is retired");
        });

        test("reflag_keeps_subscribers_informed", () -> {
            PortDeskApi desk = newDesk();
            desk.subscribe("Coppermine", "agent-mia");
            desk.book("Coppermine", "NQ1", 10, 14);
            desk.renameVessel("Coppermine", "Novara Star");
            desk.amendEnd("BK-1", 17);
            desk.cancel("BK-1");
            eq(desk.inbox("agent-mia"),
                    List.of("BOOKED BK-1 NQ1 10-14", "AMENDED BK-1 NQ1 10-17", "CANCELLED BK-1"),
                    "subscriber follows the vessel through a reflag");
        });

        test("late_subscriber_to_new_name", () -> {
            PortDeskApi desk = newDesk();
            desk.book("Coppermine", "NQ1", 10, 14);
            desk.renameVessel("Coppermine", "Novara Star");
            desk.subscribe("Novara Star", "agent-rex");
            desk.amendEnd("BK-1", 16);
            eq(desk.inbox("agent-rex"), List.of("AMENDED BK-1 NQ1 10-16"), "post-reflag subscriber");
            throwsCode("UNKNOWN_VESSEL", () -> desk.subscribe("Coppermine", "agent-lou"),
                    "old name not subscribable");
        });

        test("unknown_and_duplicate_ids_surface_codes", () -> {
            PortDeskApi desk = newDesk();
            throwsCode("UNKNOWN_BOOKING", () -> desk.quoteCents("BK-9"), "quote unknown booking");
            throwsCode("UNKNOWN_BERTH", () -> desk.availability("XX9", 8, 10), "board unknown berth");
            throwsCode("UNKNOWN_VESSEL", () -> desk.book("Phantom", "NQ1", 8, 10), "book unknown vessel");
            throwsCode("DUPLICATE_BERTH", () -> desk.addBerth("NQ1", 60, 120, 900), "berth code reuse");
            throwsCode("DUPLICATE_VESSEL", () -> desk.registerVessel("Coppermine", 50, 90),
                    "vessel name reuse");
            desk.registerVessel("Gritstone", 64, 118);
            throwsCode("DUPLICATE_VESSEL", () -> desk.renameVessel("Gritstone", "Coppermine"),
                    "rename onto a live name");
        });

        System.out.println();
        System.out.println("TOTAL: " + passed + " passed, " + failed + " failed");
        System.exit(failed > 0 ? 1 : 0);
    }
}
