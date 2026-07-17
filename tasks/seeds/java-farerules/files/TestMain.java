import java.time.LocalDate;
import java.util.List;

/**
 * Acceptance contract for the fare rules engine.
 * Every explanation string in here is pinned: revenue-ops greps the change
 * log for these exact lines, so treat them as API.
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

    static void yes(boolean cond, String what) {
        if (!cond) {
            throw new AssertionError(what);
        }
    }

    // ------------------------------------------------------------------
    // Fixtures: the four published fare classes on the DEN-MSY city pair.

    static final FareClass Q00 = new FareClass("Q00", 12900, 21, 0, false, 0);
    static final FareClass K14 = new FareClass("K14", 19900, 14, 7, true, 7500);
    static final FareClass M21 = new FareClass("M21", 22900, 7, 3, true, 7500);
    static final FareClass J77 = new FareClass("J77", 41900, 0, 0, true, 0);

    static LocalDate d(String iso) {
        return LocalDate.parse(iso);
    }

    static TripRequest trip(String booking, String departure, String ret) {
        return new TripRequest(d(booking), d(departure), ret == null ? null : d(ret));
    }

    public static void main(String[] args) {

        test("money_formatting", () -> {
            eq(FareRules.money(0), "$0.00", "zero");
            eq(FareRules.money(5), "$0.05", "five cents");
            eq(FareRules.money(105), "$1.05", "one-oh-five");
            eq(FareRules.money(19900), "$199.00", "K14 base");
            eq(FareRules.money(12345), "$123.45", "arbitrary");
        });

        test("advance_purchase_boundary_pass", () -> {
            Decision dec = FareRules.evaluate(K14, trip("2026-07-01", "2026-07-15", "2026-07-25"));
            yes(dec.eligible(), "exactly 14 days out must be eligible");
            eq(dec.explanations(), List.of(
                    "PASS advance-purchase: needs 14 days, has 14",
                    "PASS min-stay: needs 7 nights, has 10"), "explanations");
        });

        test("advance_purchase_one_day_short", () -> {
            Decision dec = FareRules.evaluate(K14, trip("2026-07-01", "2026-07-14", "2026-07-25"));
            yes(!dec.eligible(), "13 days out must be ineligible");
            eq(dec.explanations(), List.of(
                    "FAIL advance-purchase: needs 14 days, has 13",
                    "PASS min-stay: needs 7 nights, has 11"), "explanations");
        });

        test("advance_purchase_not_required", () -> {
            Decision dec = FareRules.evaluate(J77, trip("2026-07-01", "2026-07-01", null));
            yes(dec.eligible(), "walk-up flex fare, same-day one-way");
            eq(dec.explanations(), List.of(
                    "PASS advance-purchase: not required",
                    "PASS min-stay: not required"), "explanations");
        });

        test("min_stay_boundary_pass", () -> {
            Decision dec = FareRules.evaluate(K14, trip("2026-07-01", "2026-08-01", "2026-08-08"));
            yes(dec.eligible(), "exactly 7 nights must be eligible");
            eq(dec.explanations(), List.of(
                    "PASS advance-purchase: needs 14 days, has 31",
                    "PASS min-stay: needs 7 nights, has 7"), "explanations");
        });

        test("min_stay_one_night_short", () -> {
            Decision dec = FareRules.evaluate(K14, trip("2026-07-01", "2026-08-01", "2026-08-07"));
            yes(!dec.eligible(), "6 nights must be ineligible");
            eq(dec.explanations(), List.of(
                    "PASS advance-purchase: needs 14 days, has 31",
                    "FAIL min-stay: needs 7 nights, has 6"), "explanations");
        });

        test("min_stay_requires_round_trip", () -> {
            Decision dec = FareRules.evaluate(K14, trip("2026-07-01", "2026-08-01", null));
            yes(!dec.eligible(), "one-way on a min-stay fare must be ineligible");
            eq(dec.explanations(), List.of(
                    "PASS advance-purchase: needs 14 days, has 31",
                    "FAIL min-stay: requires a round trip"), "explanations");
        });

        test("same_day_return_is_zero_nights", () -> {
            Decision dec = FareRules.evaluate(M21, trip("2026-07-01", "2026-07-08", "2026-07-08"));
            yes(!dec.eligible(), "0 nights on a 3-night fare");
            eq(dec.explanations(), List.of(
                    "PASS advance-purchase: needs 7 days, has 7",
                    "FAIL min-stay: needs 3 nights, has 0"), "explanations");
        });

        test("both_rules_fail_in_precedence_order", () -> {
            Decision dec = FareRules.evaluate(K14, trip("2026-07-05", "2026-07-10", "2026-07-12"));
            yes(!dec.eligible(), "fails both rules");
            eq(dec.explanations(), List.of(
                    "FAIL advance-purchase: needs 14 days, has 5",
                    "FAIL min-stay: needs 7 nights, has 2"), "advance-purchase reports before min-stay");
        });

        test("booking_window_departure_before_booking_is_terminal", () -> {
            Decision dec = FareRules.evaluate(K14, trip("2026-07-10", "2026-07-01", "2026-07-02"));
            yes(!dec.eligible(), "departure in the past");
            eq(dec.explanations(), List.of(
                    "FAIL booking-window: departure 2026-07-01 is before booking date 2026-07-10"),
                    "terminal: no advance-purchase or min-stay lines may follow");
        });

        test("booking_window_return_before_departure_is_terminal", () -> {
            Decision dec = FareRules.evaluate(K14, trip("2026-07-01", "2026-08-10", "2026-08-01"));
            yes(!dec.eligible(), "return before departure");
            eq(dec.explanations(), List.of(
                    "FAIL booking-window: return 2026-08-01 is before departure 2026-08-10"),
                    "terminal: exactly one line");
        });

        test("cheapest_picks_lowest_base", () -> {
            Quote q = FareRules.cheapestEligible(List.of(Q00, K14, M21, J77),
                    trip("2026-06-01", "2026-07-15", "2026-07-30"));
            eq(q.fareCode(), "Q00", "winner");
            eq(q.priceCents(), 12900L, "winning price");
            eq(q.verdicts(), List.of(
                    "Q00: eligible at $129.00",
                    "K14: eligible at $199.00",
                    "M21: eligible at $229.00",
                    "J77: eligible at $419.00",
                    "selected Q00 at $129.00"), "verdicts");
        });

        test("cheapest_reports_first_failing_rule", () -> {
            Quote q = FareRules.cheapestEligible(List.of(Q00, K14, M21, J77),
                    trip("2026-07-01", "2026-07-11", "2026-07-16"));
            eq(q.fareCode(), "M21", "winner");
            eq(q.priceCents(), 22900L, "winning price");
            eq(q.verdicts(), List.of(
                    "Q00: ineligible (advance-purchase)",
                    "K14: ineligible (advance-purchase)",
                    "M21: eligible at $229.00",
                    "J77: eligible at $419.00",
                    "selected M21 at $229.00"),
                    "K14 fails min-stay too, but advance-purchase comes first");
        });

        test("cheapest_one_way_min_stay_reason", () -> {
            Quote q = FareRules.cheapestEligible(List.of(Q00, K14, M21, J77),
                    trip("2026-06-01", "2026-07-15", null));
            eq(q.fareCode(), "Q00", "winner");
            eq(q.verdicts(), List.of(
                    "Q00: eligible at $129.00",
                    "K14: ineligible (min-stay)",
                    "M21: ineligible (min-stay)",
                    "J77: eligible at $419.00",
                    "selected Q00 at $129.00"), "verdicts");
        });

        test("cheapest_tie_breaks_on_code", () -> {
            FareClass zz9 = new FareClass("ZZ9", 19900, 0, 0, true, 0);
            FareClass aa1 = new FareClass("AA1", 19900, 0, 0, true, 0);
            Quote q = FareRules.cheapestEligible(List.of(zz9, aa1),
                    trip("2026-07-01", "2026-07-02", null));
            eq(q.fareCode(), "AA1", "lexicographically smaller code wins the tie");
            eq(q.priceCents(), 19900L, "price");
            eq(q.verdicts(), List.of(
                    "ZZ9: eligible at $199.00",
                    "AA1: eligible at $199.00",
                    "selected AA1 at $199.00"), "verdicts stay in input order");
        });

        test("cheapest_none_eligible", () -> {
            Quote q = FareRules.cheapestEligible(List.of(Q00, K14, M21),
                    trip("2026-07-14", "2026-07-15", "2026-07-16"));
            eq(q.fareCode(), null, "no winner");
            eq(q.priceCents(), 0L, "no price");
            eq(q.verdicts(), List.of(
                    "Q00: ineligible (advance-purchase)",
                    "K14: ineligible (advance-purchase)",
                    "M21: ineligible (advance-purchase)",
                    "no eligible fare"), "verdicts");
        });

        test("cheapest_empty_fare_list", () -> {
            Quote q = FareRules.cheapestEligible(List.of(),
                    trip("2026-07-01", "2026-07-15", null));
            eq(q.fareCode(), null, "no winner");
            eq(q.priceCents(), 0L, "no price");
            eq(q.verdicts(), List.of("no eligible fare"), "verdicts");
        });

        test("cheapest_booking_window_reason", () -> {
            Quote q = FareRules.cheapestEligible(List.of(J77),
                    trip("2026-07-10", "2026-07-01", "2026-07-20"));
            eq(q.fareCode(), null, "no winner");
            eq(q.verdicts(), List.of(
                    "J77: ineligible (booking-window)",
                    "no eligible fare"), "verdicts");
        });

        test("change_denied_when_fare_not_changeable", () -> {
            ChangeQuote cq = FareRules.change(Q00, K14, d("2026-06-15"),
                    trip("2026-07-01", "2026-08-01", "2026-08-10"));
            yes(!cq.allowed(), "Q00 is use-it-or-lose-it");
            eq(cq.feeCents(), 0L, "fee");
            eq(cq.fareDifferenceCents(), 0L, "difference");
            eq(cq.totalDueCents(), 0L, "total");
            eq(cq.explanations(), List.of(
                    "DENY change: fare Q00 does not permit changes"),
                    "changeability is checked before anything else");
        });

        test("change_denied_when_new_fare_fails_rules", () -> {
            ChangeQuote cq = FareRules.change(J77, Q00, d("2026-06-01"),
                    trip("2026-07-10", "2026-07-20", null));
            yes(!cq.allowed(), "Q00 needs 21 days as of the change date");
            eq(cq.totalDueCents(), 0L, "total");
            eq(cq.explanations(), List.of(
                    "DENY change: fare Q00 fails its rules",
                    "FAIL advance-purchase: needs 21 days, has 10",
                    "PASS min-stay: not required"),
                    "deny line first, then the new fare's full rule report");
        });

        test("change_denied_when_new_departure_before_change_date", () -> {
            ChangeQuote cq = FareRules.change(J77, J77, d("2026-06-01"),
                    trip("2026-07-10", "2026-07-05", null));
            yes(!cq.allowed(), "cannot rebook onto a flight in the past");
            eq(cq.explanations(), List.of(
                    "DENY change: fare J77 fails its rules",
                    "FAIL booking-window: departure 2026-07-05 is before booking date 2026-07-10"),
                    "explanations");
        });

        test("change_fee_plus_upgrade_difference", () -> {
            ChangeQuote cq = FareRules.change(K14, M21, d("2026-06-15"),
                    trip("2026-07-10", "2026-07-20", "2026-07-25"));
            yes(cq.allowed(), "M21 is bookable 10 days out with a 5-night stay");
            eq(cq.feeCents(), 7500L, "old fare's change fee");
            eq(cq.fareDifferenceCents(), 3000L, "upgrade difference");
            eq(cq.totalDueCents(), 10500L, "fee + difference");
            eq(cq.explanations(), List.of(
                    "ALLOW change: K14 -> M21",
                    "FEE change: $75.00",
                    "FARE-DIFFERENCE: $30.00",
                    "TOTAL DUE: $105.00"), "explanations");
        });

        test("change_same_day_void_window_waives_fee", () -> {
            ChangeQuote cq = FareRules.change(K14, M21, d("2026-07-10"),
                    trip("2026-07-10", "2026-07-20", "2026-07-25"));
            yes(cq.allowed(), "void-window change");
            eq(cq.feeCents(), 0L, "fee waived");
            eq(cq.fareDifferenceCents(), 3000L, "difference still due");
            eq(cq.totalDueCents(), 3000L, "total");
            eq(cq.explanations(), List.of(
                    "ALLOW change: K14 -> M21",
                    "WAIVE fee: change within the same-day void window",
                    "FARE-DIFFERENCE: $30.00",
                    "TOTAL DUE: $30.00"), "explanations");
        });

        test("change_downgrade_gives_no_refund", () -> {
            ChangeQuote cq = FareRules.change(M21, K14, d("2026-06-15"),
                    trip("2026-07-01", "2026-07-20", "2026-07-28"));
            yes(cq.allowed(), "K14 is bookable 19 days out with an 8-night stay");
            eq(cq.feeCents(), 7500L, "fee");
            eq(cq.fareDifferenceCents(), 0L, "difference clamps at zero");
            eq(cq.totalDueCents(), 7500L, "total");
            eq(cq.explanations(), List.of(
                    "ALLOW change: M21 -> K14",
                    "FEE change: $75.00",
                    "FARE-DIFFERENCE: $0.00 (no refund on downgrade)",
                    "TOTAL DUE: $75.00"), "explanations");
        });

        test("change_zero_fee_outside_void_window_still_says_fee", () -> {
            ChangeQuote cq = FareRules.change(J77, J77, d("2026-06-15"),
                    trip("2026-07-10", "2026-07-12", null));
            yes(cq.allowed(), "flex fare change");
            eq(cq.feeCents(), 0L, "J77 has no change fee");
            eq(cq.fareDifferenceCents(), 0L, "same fare");
            eq(cq.totalDueCents(), 0L, "total");
            eq(cq.explanations(), List.of(
                    "ALLOW change: J77 -> J77",
                    "FEE change: $0.00",
                    "FARE-DIFFERENCE: $0.00 (no refund on downgrade)",
                    "TOTAL DUE: $0.00"), "FEE line, not WAIVE: only the void window waives");
        });

        System.out.println();
        System.out.println("TOTAL: " + passed + " passed, " + failed + " failed");
        System.exit(failed > 0 ? 1 : 0);
    }
}
