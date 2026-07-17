import java.util.List;
import java.util.Locale;
import java.util.NoSuchElementException;

/**
 * Acceptance contract for the trade-billing service: line math, discount
 * terms, memoized totals, and adjustment settlement. All money figures are
 * integer cents and every expected value below was recomputed by hand from
 * the fixture quantities and unit prices.
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

    static void throwsA(Class<? extends Throwable> type, Body body, String what) {
        try {
            body.run();
        } catch (Throwable t) {
            if (type.isInstance(t)) {
                return;
            }
            throw new AssertionError(what + ": expected " + type.getSimpleName() + " got " + t);
        }
        throw new AssertionError(what + ": expected " + type.getSimpleName() + ", nothing thrown");
    }

    public static void main(String[] args) {
        Locale.setDefault(Locale.ROOT);

        test("line_totals_and_subtotal", () -> {
            BillingService svc = new BillingService();
            svc.open("INV-1001", "Sheardale Metal Works", 0, 0);
            svc.get("INV-1001").addLine("FB-2050", "flat bar 2x1/4 A36 20ft", 12, 4550);
            svc.get("INV-1001").addLine("RB-1969", "round bar 1in 1018 12ft", 25, 1969);
            // 12*4550 + 25*1969 = 54600 + 49225 = 103825
            eq(Pricing.merchandiseSubtotal(svc.get("INV-1001")), 103825L, "subtotal");
            eq(svc.totalDue("INV-1001"), 103825L, "total with no terms");
        });

        test("trade_discount_rounds_half_up", () -> {
            BillingService svc = new BillingService();
            svc.open("INV-1002", "Karsten Fabrication", 750, 0);
            svc.get("INV-1002").addLine("FB-6120", "flat bar 4x3/8 A36 20ft", 30, 6120);
            svc.get("INV-1002").addLine("AN-7513", "angle 3x3x1/4 A36 20ft", 6, 2450);
            // subtotal 198300; 7.5% = 14872.50 -> half up 14873
            eq(Pricing.tradeDiscount(svc.get("INV-1002")), 14873L, "trade discount");
            eq(svc.totalDue("INV-1002"), 183427L, "total after trade discount");
        });

        test("cached_total_stable_across_reads", () -> {
            BillingService svc = new BillingService();
            svc.open("INV-1003", "Sheardale Metal Works", 0, 0);
            svc.get("INV-1003").addLine("FB-2050", "flat bar 2x1/4 A36 20ft", 12, 4550);
            eq(svc.totalDue("INV-1003"), 54600L, "first read");
            eq(svc.totalDue("INV-1003"), 54600L, "second read");
        });

        test("quantity_change_updates_total", () -> {
            BillingService svc = new BillingService();
            svc.open("INV-1004", "Sheardale Metal Works", 0, 0);
            svc.get("INV-1004").addLine("FB-2050", "flat bar 2x1/4 A36 20ft", 12, 4550);
            eq(svc.totalDue("INV-1004"), 54600L, "before edit");
            svc.get("INV-1004").setQuantity("FB-2050", 20);
            eq(svc.totalDue("INV-1004"), 91000L, "after quantity edit");
        });

        test("added_line_updates_total", () -> {
            BillingService svc = new BillingService();
            svc.open("INV-1005", "Sheardale Metal Works", 0, 0);
            svc.get("INV-1005").addLine("FB-2050", "flat bar 2x1/4 A36 20ft", 12, 4550);
            eq(svc.totalDue("INV-1005"), 54600L, "before new line");
            svc.get("INV-1005").addLine("PL-8140", "plate 3/8x60 A36 sheared", 3, 8140);
            eq(svc.totalDue("INV-1005"), 79020L, "after new line");
        });

        test("prompt_pay_lands_on_whole_cents", () -> {
            BillingService svc = new BillingService();
            svc.open("INV-1006", "Bellhaven Trailer", 0, 200);
            svc.get("INV-1006").addLine("FB-2050", "flat bar 2x1/4 A36 20ft", 12, 4550);
            svc.get("INV-1006").addLine("RB-1969", "round bar 1in 1018 12ft", 25, 1969);
            // 2% of 103825 = 2076.50 -> half up 2077
            eq(Pricing.promptPayDiscount(svc.get("INV-1006")), 2077L, "prompt-pay discount");
            eq(svc.totalDue("INV-1006"), 101748L, "total under prompt-pay terms");
        });

        test("prompt_pay_stacks_on_trade_terms", () -> {
            BillingService svc = new BillingService();
            svc.open("INV-1007", "Karsten Fabrication", 750, 200);
            svc.get("INV-1007").addLine("FB-6120", "flat bar 4x3/8 A36 20ft", 8, 6120);
            svc.get("INV-1007").addLine("FB-2050", "flat bar 2x1/4 A36 20ft", 5, 4550);
            // subtotal 71710; trade 5378 (5378.25 rounds to 5378); base 66332;
            // 2% of 66332 = 1326.64 -> half up 1327
            eq(Pricing.promptPayDiscount(svc.get("INV-1007")), 1327L, "stacked prompt-pay discount");
            eq(svc.totalDue("INV-1007"), 65005L, "total under both terms");
        });

        test("freight_under_cap_settles_at_cost", () -> {
            BillingService svc = new BillingService();
            svc.open("INV-1008", "Bellhaven Trailer", 0, 0);
            svc.get("INV-1008").addLine("PL-6000", "plate 1/4x48 A36", 40, 6000);
            svc.get("INV-1008").addAdjustment("FRT", Invoice.AdjustmentKind.FREIGHT, 11000);
            svc.get("INV-1008").addAdjustment("PLT", Invoice.AdjustmentKind.PALLET_FEE, 1500);
            // merchandise 240000, cap 12000, freight under cap
            eq(svc.settle("INV-1008"), 252500L, "settled total");
            eq(svc.ledger("INV-1008"), List.of("FRT +11000", "PLT +1500"), "ledger");
        });

        test("freight_over_cap_posts_credit", () -> {
            BillingService svc = new BillingService();
            svc.open("INV-1009", "Bellhaven Trailer", 0, 0);
            svc.get("INV-1009").addLine("PL-6000", "plate 1/4x48 A36", 40, 6000);
            svc.get("INV-1009").addAdjustment("FRT", Invoice.AdjustmentKind.FREIGHT, 15000);
            // merchandise 240000, cap 12000: freight posts at cost, then a
            // -3000 FRT-CAP credit brings it back to the cap
            eq(svc.settle("INV-1009"), 252000L, "settled total");
            eq(svc.ledger("INV-1009"), List.of("FRT +15000", "FRT-CAP -3000"), "ledger");
        });

        test("statement_pins_revision_and_format", () -> {
            BillingService svc = new BillingService();
            svc.open("INV-9001", "Sheardale Metal Works", 0, 0);
            svc.get("INV-9001").addLine("TB-1533", "square tube 2x2x11ga 24ft", 3, 1533);
            eq(svc.statement("INV-9001"), "INV-9001 rev 1 due 45.99 USD", "statement");
        });

        test("unknown_invoice_and_sku_rejected", () -> {
            BillingService svc = new BillingService();
            throwsA(NoSuchElementException.class, () -> svc.totalDue("INV-404"), "unknown invoice");
            svc.open("INV-1010", "Sheardale Metal Works", 0, 0);
            svc.get("INV-1010").addLine("FB-2050", "flat bar 2x1/4 A36 20ft", 1, 4550);
            throwsA(NoSuchElementException.class,
                    () -> svc.get("INV-1010").setQuantity("ZZ-0000", 2), "unknown sku");
            throwsA(IllegalArgumentException.class,
                    () -> svc.open("INV-1010", "Sheardale Metal Works", 0, 0), "duplicate open");
        });

        test("rejects_bad_quantities", () -> {
            BillingService svc = new BillingService();
            svc.open("INV-1011", "Sheardale Metal Works", 0, 0);
            throwsA(IllegalArgumentException.class,
                    () -> svc.get("INV-1011").addLine("FB-2050", "flat bar", 0, 4550), "zero quantity");
            svc.get("INV-1011").addLine("FB-2050", "flat bar 2x1/4 A36 20ft", 2, 4550);
            throwsA(IllegalArgumentException.class,
                    () -> svc.get("INV-1011").setQuantity("FB-2050", 0), "zero on edit");
        });

        test("month_end_settle_end_to_end", () -> {
            BillingService svc = new BillingService();
            svc.open("INV-4402", "Karsten Fabrication", 750, 200);
            svc.get("INV-4402").addLine("FB-6120", "flat bar 4x3/8 A36 20ft", 30, 6120);
            svc.get("INV-4402").addLine("AN-7513", "angle 3x3x1/4 A36 20ft", 15, 2450);
            // subtotal 220350; trade 16526 (16526.25); base 203824; 2% = 4076.48 -> 4076
            eq(svc.totalDue("INV-4402"), 199748L, "draft total before the edit");
            svc.get("INV-4402").setQuantity("AN-7513", 6);
            // subtotal 198300; trade 14873 (14872.50 half up); base 183427;
            // 2% = 3668.54 -> half up 3669; due 179758
            eq(svc.totalDue("INV-4402"), 179758L, "draft total after the edit");
            svc.get("INV-4402").addAdjustment("FRT", Invoice.AdjustmentKind.FREIGHT, 12400);
            svc.get("INV-4402").addAdjustment("PLT", Invoice.AdjustmentKind.PALLET_FEE, 1500);
            // cap = 5% of 198300 = 9915; credit -2485; 179758+12400+1500-2485
            eq(svc.settle("INV-4402"), 191173L, "settled total");
            eq(svc.ledger("INV-4402"),
                    List.of("FRT +12400", "PLT +1500", "FRT-CAP -2485"), "ledger order");
            eq(svc.statement("INV-4402"), "INV-4402 rev 6 due 1797.58 USD", "statement");
        });

        System.out.println();
        System.out.println("TOTAL: " + passed + " passed, " + failed + " failed");
        System.exit(failed > 0 ? 1 : 0);
    }
}
