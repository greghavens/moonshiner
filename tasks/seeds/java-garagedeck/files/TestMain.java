/**
 * garagedeck acceptance suite. Run with: java TestMain.java
 *
 * Plain-conditional checks only (the JVM `assert` keyword is never used, the
 * suite runs without -ea). Prints one PASS/FAIL line per test and exits 1
 * when anything fails.
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
        if (!java.util.Objects.equals(actual, expected)) {
            throw new AssertionError(what + ": expected <" + expected + "> got <" + actual + ">");
        }
    }

    static void eqLong(long actual, long expected, String what) {
        if (actual != expected) {
            throw new AssertionError(what + ": expected <" + expected + "> got <" + actual + ">");
        }
    }

    static void yes(boolean cond, String what) {
        if (!cond) {
            throw new AssertionError(what);
        }
    }

    static void no(boolean cond, String what) {
        if (cond) {
            throw new AssertionError(what);
        }
    }

    static void throwsMsg(Class<? extends RuntimeException> type, String message, Runnable action) {
        boolean threw = false;
        try {
            action.run();
        } catch (RuntimeException e) {
            threw = true;
            if (!type.isInstance(e)) {
                throw new AssertionError("expected " + type.getSimpleName() + " got "
                        + e.getClass().getSimpleName() + " (" + e.getMessage() + ")");
            }
            if (!message.equals(e.getMessage())) {
                throw new AssertionError("expected message <" + message + "> got <" + e.getMessage() + ">");
            }
        }
        if (!threw) {
            throw new AssertionError("expected " + type.getSimpleName() + " <" + message
                    + "> but nothing was thrown");
        }
    }

    public static void main(String[] args) {

        // ---------- VehicleClass ----------

        test("fit_rules", () -> {
            yes(VehicleClass.MOTORCYCLE.fitsIn(VehicleClass.MOTORCYCLE), "M fits M");
            yes(VehicleClass.MOTORCYCLE.fitsIn(VehicleClass.COMPACT), "M fits C");
            yes(VehicleClass.MOTORCYCLE.fitsIn(VehicleClass.STANDARD), "M fits S");
            yes(VehicleClass.MOTORCYCLE.fitsIn(VehicleClass.OVERSIZE), "M fits V");
            no(VehicleClass.COMPACT.fitsIn(VehicleClass.MOTORCYCLE), "C does not fit M");
            yes(VehicleClass.COMPACT.fitsIn(VehicleClass.COMPACT), "C fits C");
            yes(VehicleClass.COMPACT.fitsIn(VehicleClass.STANDARD), "C fits S");
            yes(VehicleClass.COMPACT.fitsIn(VehicleClass.OVERSIZE), "C fits V");
            no(VehicleClass.STANDARD.fitsIn(VehicleClass.COMPACT), "S does not fit C");
            yes(VehicleClass.STANDARD.fitsIn(VehicleClass.STANDARD), "S fits S");
            yes(VehicleClass.STANDARD.fitsIn(VehicleClass.OVERSIZE), "S fits V");
            no(VehicleClass.OVERSIZE.fitsIn(VehicleClass.STANDARD), "V does not fit S");
            yes(VehicleClass.OVERSIZE.fitsIn(VehicleClass.OVERSIZE), "V fits V");
        });

        test("class_codes", () -> {
            eq(VehicleClass.MOTORCYCLE.code(), "M", "motorcycle code");
            eq(VehicleClass.COMPACT.code(), "C", "compact code");
            eq(VehicleClass.STANDARD.code(), "S", "standard code");
            eq(VehicleClass.OVERSIZE.code(), "V", "oversize code");
        });

        // ---------- SpaceInventory ----------

        test("inventory_ids_and_counts", () -> {
            SpaceInventory inv = new SpaceInventory(2, 3, 4, 1);
            eqLong(inv.total(VehicleClass.MOTORCYCLE), 2, "total M");
            eqLong(inv.total(VehicleClass.COMPACT), 3, "total C");
            eqLong(inv.total(VehicleClass.STANDARD), 4, "total S");
            eqLong(inv.total(VehicleClass.OVERSIZE), 1, "total V");
            eqLong(inv.free(VehicleClass.MOTORCYCLE), 2, "free M before assigning");
            eq(inv.assign(VehicleClass.MOTORCYCLE), "M-01", "first motorcycle space");
            eqLong(inv.free(VehicleClass.MOTORCYCLE), 1, "free M after one");
            yes(inv.isOccupied("M-01"), "M-01 occupied");
            no(inv.isOccupied("M-02"), "M-02 still free");
            eq(inv.assign(VehicleClass.MOTORCYCLE), "M-02", "second motorcycle space");
            eqLong(inv.free(VehicleClass.MOTORCYCLE), 0, "free M exhausted");
        });

        test("assign_prefers_smallest_fitting_class", () -> {
            SpaceInventory inv = new SpaceInventory(1, 1, 1, 1);
            eq(inv.assign(VehicleClass.COMPACT), "C-01", "own class first");
            eq(inv.assign(VehicleClass.COMPACT), "S-01", "overflow to standard");
            eq(inv.assign(VehicleClass.COMPACT), "V-01", "overflow to oversize");
            eq(inv.assign(VehicleClass.COMPACT), null, "nothing left that fits");
            SpaceInventory inv2 = new SpaceInventory(0, 1, 0, 0);
            eq(inv2.assign(VehicleClass.MOTORCYCLE), "C-01", "motorcycle overflows to compact");
            SpaceInventory inv3 = new SpaceInventory(5, 5, 5, 0);
            eq(inv3.assign(VehicleClass.OVERSIZE), null, "oversize only fits oversize");
        });

        test("assign_lowest_number_and_reuse", () -> {
            SpaceInventory inv = new SpaceInventory(0, 3, 0, 0);
            eq(inv.assign(VehicleClass.COMPACT), "C-01", "first assignment");
            eq(inv.assign(VehicleClass.COMPACT), "C-02", "second assignment");
            inv.release("C-01");
            no(inv.isOccupied("C-01"), "released space is free");
            eq(inv.assign(VehicleClass.COMPACT), "C-01", "lowest-numbered free space is reused");
        });

        test("release_errors", () -> {
            SpaceInventory inv = new SpaceInventory(1, 1, 0, 0);
            throwsMsg(IllegalArgumentException.class, "unknown space X-99", () -> inv.release("X-99"));
            throwsMsg(IllegalArgumentException.class, "unknown space S-01", () -> inv.release("S-01"));
            throwsMsg(IllegalStateException.class, "space M-01 is not occupied", () -> inv.release("M-01"));
        });

        // ---------- Ticket / TicketDesk ----------

        test("ticket_issue_and_fields", () -> {
            TicketDesk desk = new TicketDesk();
            Ticket t = desk.issue("AAA-111", VehicleClass.COMPACT, "C-01", 5);
            eq(t.id(), "T-0001", "first ticket id");
            eq(t.plate(), "AAA-111", "plate");
            eq(t.vehicleClass(), VehicleClass.COMPACT, "vehicle class");
            eq(t.spaceId(), "C-01", "space id");
            eqLong(t.entryTick(), 5, "entry tick");
            eq(t.status(), Ticket.Status.OPEN, "fresh ticket is open");
            Ticket t2 = desk.issue("BBB-222", VehicleClass.STANDARD, "S-01", 9);
            eq(t2.id(), "T-0002", "ids are sequential");
            eq(desk.lookup("T-0001").plate(), "AAA-111", "lookup returns the ticket");
        });

        test("ticket_lookup_unknown", () -> {
            TicketDesk desk = new TicketDesk();
            desk.issue("AAA-111", VehicleClass.COMPACT, "C-01", 0);
            throwsMsg(IllegalArgumentException.class, "unknown ticket T-0009", () -> desk.lookup("T-0009"));
        });

        test("ticket_lifecycle_guards", () -> {
            TicketDesk desk = new TicketDesk();
            desk.issue("AAA-111", VehicleClass.COMPACT, "C-01", 0);
            desk.voidTicket("T-0001");
            eq(desk.lookup("T-0001").status(), Ticket.Status.VOID, "voided ticket");
            throwsMsg(IllegalStateException.class, "ticket T-0001 is not open", () -> desk.voidTicket("T-0001"));
            throwsMsg(IllegalStateException.class, "ticket T-0001 is not open", () -> desk.markPaid("T-0001"));
            desk.issue("BBB-222", VehicleClass.STANDARD, "S-01", 4);
            desk.markPaid("T-0002");
            eq(desk.lookup("T-0002").status(), Ticket.Status.PAID, "paid ticket");
            throwsMsg(IllegalStateException.class, "ticket T-0002 is not open", () -> desk.voidTicket("T-0002"));
        });

        // ---------- Pricing ----------

        test("pricing_first_hour", () -> {
            eqLong(Pricing.charge(VehicleClass.STANDARD, 0), 600, "0 minutes still bills the first hour");
            eqLong(Pricing.charge(VehicleClass.STANDARD, 25), 600, "25 minutes");
            eqLong(Pricing.charge(VehicleClass.STANDARD, 60), 600, "exactly one hour");
            eqLong(Pricing.charge(VehicleClass.STANDARD, 61), 1000, "61 minutes starts hour two");
            throwsMsg(IllegalArgumentException.class, "negative duration",
                    () -> Pricing.charge(VehicleClass.STANDARD, -1));
        });

        test("pricing_hour_tiers", () -> {
            eqLong(Pricing.charge(VehicleClass.STANDARD, 180), 1400, "standard 3h: 600 + 2*400");
            eqLong(Pricing.charge(VehicleClass.MOTORCYCLE, 120), 500, "motorcycle 2h: 300 + 200");
            eqLong(Pricing.charge(VehicleClass.COMPACT, 181), 1550, "compact 181min rounds up to 4h");
            eqLong(Pricing.charge(VehicleClass.OVERSIZE, 120), 1700, "oversize 2h: 1000 + 700");
        });

        test("pricing_daily_cap", () -> {
            eqLong(Pricing.charge(VehicleClass.STANDARD, 480), 3000, "standard 8h capped at daily max");
            eqLong(Pricing.charge(VehicleClass.COMPACT, 720), 2500, "compact 12h capped at daily max");
            eqLong(Pricing.charge(VehicleClass.MOTORCYCLE, 359), 1300, "motorcycle 6h stays under the cap");
        });

        test("pricing_multi_day", () -> {
            eqLong(Pricing.charge(VehicleClass.STANDARD, 1440), 3000, "exactly one day");
            eqLong(Pricing.charge(VehicleClass.STANDARD, 1441), 3600, "one day plus one minute");
            eqLong(Pricing.charge(VehicleClass.STANDARD, 1800), 5600, "one day six hours");
            eqLong(Pricing.charge(VehicleClass.STANDARD, 2880), 6000, "exactly two days");
        });

        test("pricing_remainder_capped", () -> {
            eqLong(Pricing.charge(VehicleClass.MOTORCYCLE, 2280), 3000,
                    "day-two remainder (14h) capped at the daily max");
        });

        test("money_format", () -> {
            eq(Pricing.money(0), "$0.00", "zero");
            eq(Pricing.money(5), "$0.05", "five cents");
            eq(Pricing.money(1750), "$17.50", "dollars and cents");
            eq(Pricing.money(100013), "$1000.13", "no thousands separator");
        });

        // ---------- Validations ----------

        test("validations_flat_and_percent", () -> {
            Validations v = new Validations();
            v.defineFlat("CAFE", 500);
            v.definePercent("CINEMA", 25);
            yes(v.defined("CAFE"), "CAFE is in the book");
            no(v.defined("NOPE"), "NOPE is not in the book");
            eqLong(v.apply("CAFE", 3000), 2500, "flat subtracts");
            eqLong(v.apply("CINEMA", 3000), 2250, "percent of the current amount");
        });

        test("validations_half_up_and_floor", () -> {
            Validations v = new Validations();
            v.definePercent("CLUB", 15);
            eqLong(v.apply("CLUB", 1750), 1487, "15% of $17.50 = $2.625 rounds half-up to $2.63");
            v.defineFlat("MEGA", 5000);
            eqLong(v.apply("MEGA", 3000), 0, "flat validation floors at zero");
            eqLong(v.apply("CLUB", 0), 0, "percent of zero is zero");
        });

        test("validations_define_errors", () -> {
            Validations v = new Validations();
            v.defineFlat("CAFE", 500);
            throwsMsg(IllegalArgumentException.class, "validation CAFE already defined",
                    () -> v.defineFlat("CAFE", 100));
            throwsMsg(IllegalArgumentException.class, "validation CAFE already defined",
                    () -> v.definePercent("CAFE", 10));
            throwsMsg(IllegalArgumentException.class, "unknown validation NOPE", () -> v.apply("NOPE", 100));
            throwsMsg(IllegalArgumentException.class, "percent must be 0..100", () -> v.definePercent("BAD", 101));
            throwsMsg(IllegalArgumentException.class, "percent must be 0..100", () -> v.definePercent("BAD", -1));
            throwsMsg(IllegalArgumentException.class, "negative amount", () -> v.defineFlat("BAD", -5));
        });

        // ---------- Garage ----------

        test("garage_enter", () -> {
            Garage g = new Garage(1, 1, 2, 1);
            Ticket t1 = g.enter("AAA-111", VehicleClass.COMPACT, 0);
            eq(t1.id(), "T-0001", "first ticket");
            eq(t1.spaceId(), "C-01", "compact space");
            eqLong(t1.entryTick(), 0, "entry tick recorded");
            Ticket t2 = g.enter("BBB-222", VehicleClass.MOTORCYCLE, 3);
            eq(t2.spaceId(), "M-01", "motorcycle space");
            eqLong(g.freeSpaces(VehicleClass.COMPACT), 0, "compact row full");
            eqLong(g.occupied(VehicleClass.COMPACT), 1, "compact occupancy");
            eqLong(g.capacity(VehicleClass.COMPACT), 1, "compact capacity");
            eq(g.lookup("T-0001").plate(), "AAA-111", "garage lookup");
        });

        test("garage_full", () -> {
            Garage g = new Garage(0, 0, 1, 0);
            eq(g.enter("AAA-111", VehicleClass.STANDARD, 0).spaceId(), "S-01", "last standard space");
            throwsMsg(IllegalStateException.class, "no space available for STANDARD",
                    () -> g.enter("BBB-222", VehicleClass.STANDARD, 1));
            throwsMsg(IllegalStateException.class, "no space available for OVERSIZE",
                    () -> g.enter("CCC-333", VehicleClass.OVERSIZE, 2));
        });

        test("garage_exit_charges", () -> {
            Garage g = new Garage(0, 0, 1, 0);
            g.enter("AAA-111", VehicleClass.STANDARD, 100);
            eqLong(g.freeSpaces(VehicleClass.STANDARD), 0, "space taken");
            Garage.Receipt r = g.exit("T-0001", 580);
            eq(r.ticketId(), "T-0001", "receipt ticket id");
            eqLong(r.minutes(), 480, "parked minutes");
            eqLong(r.baseCents(), 3000, "8h standard hits the daily max");
            eqLong(r.dueCents(), 3000, "no validations");
            eqLong(r.discountCents(), 0, "no discount");
            yes(r.appliedValidations().isEmpty(), "no stamps applied");
            eq(g.lookup("T-0001").status(), Ticket.Status.PAID, "ticket settles as paid");
            eqLong(g.freeSpaces(VehicleClass.STANDARD), 1, "space released on exit");
            eqLong(g.receiptFor("T-0001").dueCents(), 3000, "receipt kept on file");
        });

        test("validation_order_matters", () -> {
            Garage g = new Garage(0, 2, 0, 0);
            g.validations().definePercent("CINEMA", 25);
            g.validations().defineFlat("CAFE", 500);
            g.enter("AAA-111", VehicleClass.COMPACT, 0);
            g.enter("BBB-222", VehicleClass.COMPACT, 0);
            g.stamp("T-0001", "CINEMA");
            g.stamp("T-0001", "CAFE");
            g.stamp("T-0002", "CAFE");
            g.stamp("T-0002", "CINEMA");
            Garage.Receipt a = g.exit("T-0001", 480);
            Garage.Receipt b = g.exit("T-0002", 480);
            eqLong(a.baseCents(), 2500, "8h compact base (capped)");
            eqLong(a.dueCents(), 1375, "percent then flat: 2500 -> 1875 -> 1375");
            eqLong(b.dueCents(), 1500, "flat then percent: 2500 -> 2000 -> 1500");
            eq(a.appliedValidations(), java.util.List.of("CINEMA", "CAFE"), "stamp order kept on receipt A");
            eq(b.appliedValidations(), java.util.List.of("CAFE", "CINEMA"), "stamp order kept on receipt B");
            eqLong(a.discountCents(), 1125, "discount A");
            eqLong(b.discountCents(), 1000, "discount B");
        });

        test("stamp_errors", () -> {
            Garage g = new Garage(1, 0, 0, 0);
            g.validations().defineFlat("CAFE", 500);
            throwsMsg(IllegalArgumentException.class, "unknown ticket T-0009", () -> g.stamp("T-0009", "CAFE"));
            g.enter("AAA-111", VehicleClass.MOTORCYCLE, 0);
            throwsMsg(IllegalArgumentException.class, "unknown validation NOPE", () -> g.stamp("T-0001", "NOPE"));
            g.stamp("T-0001", "CAFE");
            throwsMsg(IllegalStateException.class, "CAFE already stamped on T-0001", () -> g.stamp("T-0001", "CAFE"));
            g.exit("T-0001", 10);
            throwsMsg(IllegalStateException.class, "ticket T-0001 is not open", () -> g.stamp("T-0001", "CAFE"));
        });

        test("exit_errors", () -> {
            Garage g = new Garage(1, 0, 0, 0);
            throwsMsg(IllegalArgumentException.class, "unknown ticket T-0009", () -> g.exit("T-0009", 5));
            g.enter("AAA-111", VehicleClass.MOTORCYCLE, 10);
            throwsMsg(IllegalArgumentException.class, "exit before entry", () -> g.exit("T-0001", 5));
            throwsMsg(IllegalArgumentException.class, "no receipt for T-0001", () -> g.receiptFor("T-0001"));
            Garage.Receipt r = g.exit("T-0001", 10);
            eqLong(r.minutes(), 0, "zero-minute stay");
            eqLong(r.dueCents(), 300, "still bills the first hour");
            throwsMsg(IllegalStateException.class, "ticket T-0001 is not open", () -> g.exit("T-0001", 20));
        });

        test("void_releases_space", () -> {
            Garage g = new Garage(1, 0, 0, 0);
            g.enter("AAA-111", VehicleClass.MOTORCYCLE, 0);
            eqLong(g.freeSpaces(VehicleClass.MOTORCYCLE), 0, "space taken");
            g.voidTicket("T-0001");
            eq(g.lookup("T-0001").status(), Ticket.Status.VOID, "ticket voided");
            eqLong(g.freeSpaces(VehicleClass.MOTORCYCLE), 1, "space released on void");
            throwsMsg(IllegalStateException.class, "ticket T-0001 is not open", () -> g.exit("T-0001", 100));
            throwsMsg(IllegalStateException.class, "ticket T-0001 is not open", () -> g.voidTicket("T-0001"));
            throwsMsg(IllegalArgumentException.class, "unknown ticket T-0009", () -> g.voidTicket("T-0009"));
        });

        test("floor_at_zero_receipt", () -> {
            Garage g = new Garage(0, 1, 0, 0);
            g.validations().defineFlat("CAFE", 500);
            g.validations().definePercent("CINEMA", 25);
            g.enter("AAA-111", VehicleClass.COMPACT, 0);
            g.stamp("T-0001", "CAFE");
            g.stamp("T-0001", "CINEMA");
            Garage.Receipt r = g.exit("T-0001", 30);
            eqLong(r.baseCents(), 500, "1h compact");
            eqLong(r.dueCents(), 0, "flat validation floors the bill at zero");
            eqLong(r.discountCents(), 500, "discount is base minus due");
            eq(r.appliedValidations(), java.util.List.of("CAFE", "CINEMA"), "both stamps recorded");
        });

        // ---------- DayReport ----------

        test("report_full_day", () -> {
            Garage g = new Garage(2, 3, 4, 1);
            g.validations().definePercent("CINEMA", 25);
            g.validations().defineFlat("CAFE", 500);
            g.enter("AAA-111", VehicleClass.COMPACT, 0);
            g.enter("BJK-320", VehicleClass.STANDARD, 10);
            g.stamp("T-0001", "CINEMA");
            g.stamp("T-0001", "CAFE");
            g.exit("T-0001", 480);
            g.enter("KJH-102", VehicleClass.STANDARD, 610);
            g.enter("MOP-555", VehicleClass.MOTORCYCLE, 620);
            g.enter("RRT-880", VehicleClass.OVERSIZE, 700);
            g.voidTicket("T-0004");
            g.exit("T-0002", 1210);
            String expected = String.join("\n",
                    "== reconciliation @ tick 1440 ==",
                    "open tickets: 2",
                    "  T-0003 KJH-102 STANDARD S-02 in=610",
                    "  T-0005 RRT-880 OVERSIZE V-01 in=700",
                    "paid tickets: 2",
                    "  T-0001 AAA-111 COMPACT due $13.75 (base $25.00, validated $11.25)",
                    "  T-0002 BJK-320 STANDARD due $30.00 (base $30.00, validated $0.00)",
                    "voided tickets: 1",
                    "  T-0004 MOP-555 MOTORCYCLE",
                    "occupancy: M 0/2 C 0/3 S 1/4 V 1/1",
                    "revenue: base $55.00, validated $11.25, due $43.75",
                    "");
            eq(DayReport.render(g, 1440), expected, "full reconciliation report");
        });

        test("report_empty", () -> {
            Garage g = new Garage(1, 1, 1, 1);
            String expected = String.join("\n",
                    "== reconciliation @ tick 0 ==",
                    "open tickets: 0",
                    "paid tickets: 0",
                    "voided tickets: 0",
                    "occupancy: M 0/1 C 0/1 S 0/1 V 0/1",
                    "revenue: base $0.00, validated $0.00, due $0.00",
                    "");
            eq(DayReport.render(g, 0), expected, "empty-day report");
        });

        test("report_sorted_by_ticket_id", () -> {
            Garage g = new Garage(0, 0, 2, 0);
            g.enter("ZZZ-999", VehicleClass.STANDARD, 1);
            g.enter("AAA-111", VehicleClass.STANDARD, 2);
            String report = DayReport.render(g, 5);
            yes(report.contains("open tickets: 2\n"
                            + "  T-0001 ZZZ-999 STANDARD S-01 in=1\n"
                            + "  T-0002 AAA-111 STANDARD S-02 in=2\n"),
                    "open tickets sort by ticket id, not by plate");
        });

        test("report_overflow_space", () -> {
            Garage g = new Garage(0, 0, 1, 0);
            g.enter("AAA-111", VehicleClass.COMPACT, 5);
            String report = DayReport.render(g, 6);
            yes(report.contains("\n  T-0001 AAA-111 COMPACT S-01 in=5\n"),
                    "vehicle class stays COMPACT even in a standard space");
        });

        System.out.println();
        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) {
            System.exit(1);
        }
    }
}
