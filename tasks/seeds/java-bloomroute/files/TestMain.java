import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.List;

/**
 * Acceptance contract for the order pipeline: cart pricing, zone routing,
 * and the delivery promise. All fixtures are fixed dates/instants — the
 * pipeline itself must never read a clock.
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
    // Fixtures.

    static Order.Line line(String item, int quantity, String unitPrice) {
        return new Order.Line(item, quantity, new BigDecimal(unitPrice));
    }

    static Order order(String id, String zone, String placedAt, Order.Line... lines) {
        return new Order(id, zone, Instant.parse(placedAt), List.of(lines));
    }

    /** The twelve zones we onboarded across four regions. */
    static RegionRouter cityRouter() {
        RegionRouter router = new RegionRouter();
        router.register("NE-04", "Petal & Stem Uptown");
        router.register("NE-11", "Meadowbrook Florals");
        router.register("NE-22", "Verbena North");
        router.register("SW-02", "Cactus Rose Co");
        router.register("SW-07", "Desert Dahlia");
        router.register("SW-15", "Yucca & Fern");
        router.register("DT-01", "Bloomhouse Central");
        router.register("DT-03", "The Stem Bar");
        router.register("DT-09", "Gilded Lily Downtown");
        router.register("LK-05", "Lakeshore Petals");
        router.register("LK-08", "Driftwood Blooms");
        router.register("LK-12", "Harborlight Flowers");
        return router;
    }

    static LocalDate d(String iso) {
        return LocalDate.parse(iso);
    }

    public static void main(String[] args) {

        // ---------------- cart pricing ----------------

        test("subtotal_sums_the_cart", () -> {
            Order o = order("BLM-1001", "DT-01", "2026-06-05T15:00:00Z",
                    line("Ranunculus Bunch", 2, "24.50"),
                    line("Peony Dozen", 1, "26.00"));
            eq(o.subtotal(), new BigDecimal("75.00"), "subtotal");
        });

        test("big_cart_gets_free_delivery", () -> {
            Order o = order("BLM-1002", "DT-01", "2026-06-05T15:00:00Z",
                    line("Orchid Planter", 1, "80.00"));
            yes(o.qualifiesForFreeDelivery(), "an $80.00 cart is over the promo line");
            eq(o.deliveryFee(), new BigDecimal("0.00"), "fee");
            eq(o.total(), new BigDecimal("80.00"), "total");
        });

        test("exactly_seventy_five_gets_free_delivery", () -> {
            Order o = order("BLM-1003", "DT-01", "2026-06-05T15:00:00Z",
                    line("Peony Dozen", 3, "25.00"));
            yes(o.qualifiesForFreeDelivery(),
                    "the promo says $75+, and this cart is exactly $75.00");
        });

        test("small_cart_pays_delivery_fee", () -> {
            Order o = order("BLM-1004", "DT-01", "2026-06-05T15:00:00Z",
                    line("Bud Vase Trio", 1, "74.99"));
            yes(!o.qualifiesForFreeDelivery(), "a $74.99 cart misses the promo");
            eq(o.deliveryFee(), new BigDecimal("12.99"), "fee");
            eq(o.total(), new BigDecimal("87.98"), "total");
        });

        test("exact_threshold_total_charges_no_fee", () -> {
            Order o = order("BLM-1005", "DT-01", "2026-06-05T15:00:00Z",
                    line("Ranunculus Bunch", 2, "24.50"),
                    line("Peony Dozen", 1, "26.00"));
            eq(o.total(), new BigDecimal("75.00"),
                    "an exactly-$75.00 cart must not be charged the $12.99 fee");
        });

        test("gift_bundle_at_threshold_gets_free_delivery", () -> {
            Order o = order("BLM-1006", "LK-08", "2026-06-05T15:00:00Z",
                    line("Anniversary Bundle", 1, "70.00"),
                    line("Card & Ribbon", 1, "5.00"));
            yes(o.qualifiesForFreeDelivery(), "a $70.00 + $5.00 gift bundle hits the promo");
            eq(o.deliveryFee(), new BigDecimal("0.00"), "fee");
        });

        // ---------------- zone routing ----------------

        test("coverage_counts_every_zone", () -> {
            eq(cityRouter().coverage(), 12, "we onboarded twelve zones");
        });

        test("zones_route_to_their_own_shop", () -> {
            RegionRouter router = cityRouter();
            eq(router.shopFor("NE-04"), "Petal & Stem Uptown", "NE-04");
            eq(router.shopFor("NE-11"), "Meadowbrook Florals", "NE-11");
            eq(router.shopFor("NE-22"), "Verbena North", "NE-22");
            eq(router.shopFor("SW-07"), "Desert Dahlia", "SW-07");
            eq(router.shopFor("LK-12"), "Harborlight Flowers", "LK-12");
        });

        test("coverage_report_lists_all_zones_sorted", () -> {
            eq(cityRouter().zones(), List.of(
                    "DT-01", "DT-03", "DT-09",
                    "LK-05", "LK-08", "LK-12",
                    "NE-04", "NE-11", "NE-22",
                    "SW-02", "SW-07", "SW-15"), "zones");
        });

        test("unknown_zone_is_rejected", () -> {
            try {
                cityRouter().shopFor("XX-99");
                throw new AssertionError("expected IllegalArgumentException");
            } catch (IllegalArgumentException e) {
                eq(e.getMessage(), "no shop covers zone 'XX-99'", "message");
            }
        });

        test("single_shop_region_routes_fine", () -> {
            RegionRouter router = new RegionRouter();
            router.register("DT-01", "Bloomhouse Central");
            eq(router.shopFor("DT-01"), "Bloomhouse Central", "DT-01");
            eq(router.coverage(), 1, "coverage");
        });

        // ---------------- delivery promise ----------------

        test("denver_early_afternoon_is_same_day", () -> {
            // 2026-06-05T19:30Z is 1:30 pm in Denver — half an hour before cutoff.
            Order o = order("BLM-2001", "SW-02", "2026-06-05T19:30:00Z",
                    line("Peony Dozen", 1, "26.00"));
            eq(DispatchScheduler.promise(o, ZoneId.of("America/Denver")),
                    new DispatchScheduler.Promise(d("2026-06-05"), true), "promise");
        });

        test("honolulu_before_cutoff_is_same_day", () -> {
            // 2026-06-05T23:00Z is 1:00 pm in Honolulu.
            Order o = order("BLM-2002", "LK-05", "2026-06-05T23:00:00Z",
                    line("Plumeria Lei", 2, "18.00"));
            eq(DispatchScheduler.promise(o, ZoneId.of("Pacific/Honolulu")),
                    new DispatchScheduler.Promise(d("2026-06-05"), true), "promise");
        });

        test("manhattan_midnight_order_is_next_day", () -> {
            // 2026-06-05T03:59Z is 11:59 pm June 4th in New York — after cutoff,
            // so it goes out June 5th and it is NOT a same-day delivery.
            Order o = order("BLM-2003", "NE-04", "2026-06-05T03:59:00Z",
                    line("Midnight Roses", 1, "45.00"));
            eq(DispatchScheduler.promise(o, ZoneId.of("America/New_York")),
                    new DispatchScheduler.Promise(d("2026-06-05"), false), "promise");
        });

        test("manhattan_morning_is_same_day", () -> {
            // 2026-06-05T13:00Z is 9:00 am in New York.
            Order o = order("BLM-2004", "NE-04", "2026-06-05T13:00:00Z",
                    line("Sunrise Tulips", 1, "32.00"));
            eq(DispatchScheduler.promise(o, ZoneId.of("America/New_York")),
                    new DispatchScheduler.Promise(d("2026-06-05"), true), "promise");
        });

        test("london_after_cutoff_is_next_day", () -> {
            // 2026-06-05T15:30Z is 4:30 pm in London.
            Order o = order("BLM-2005", "DT-03", "2026-06-05T15:30:00Z",
                    line("English Garden Mix", 1, "38.00"));
            eq(DispatchScheduler.promise(o, ZoneId.of("Europe/London")),
                    new DispatchScheduler.Promise(d("2026-06-06"), false), "promise");
        });

        // ---------------- end to end ----------------

        test("order_confirmation_end_to_end", () -> {
            RegionRouter router = cityRouter();
            Order o = order("BLM-5501", "NE-11", "2026-06-05T19:30:00Z",
                    line("Peony Dozen", 2, "25.00"),
                    line("Eucalyptus Wrap", 1, "25.00"));
            DispatchScheduler.Promise promise =
                    DispatchScheduler.promise(o, ZoneId.of("America/Denver"));
            String confirmation = o.id() + ": " + router.shopFor(o.zone())
                    + " will deliver on " + promise.deliveryDate()
                    + (promise.sameDay() ? " (same-day)" : "")
                    + ", total $" + o.total();
            eq(confirmation,
                    "BLM-5501: Meadowbrook Florals will deliver on 2026-06-05 (same-day), total $75.00",
                    "confirmation");
        });

        System.out.println();
        System.out.println("TOTAL: " + passed + " passed, " + failed + " failed");
        System.exit(failed > 0 ? 1 : 0);
    }
}
