import java.util.List;

public class TestMain {
    private static int failures = 0;

    private static void check(boolean ok, String what) {
        if (ok) {
            System.out.println("ok   " + what);
        } else {
            failures++;
            System.out.println("FAIL " + what);
        }
    }

    private static void checkEquals(Object expected, Object actual, String what) {
        check(expected.equals(actual), what + " (expected " + expected + ", got " + actual + ")");
    }

    private static String numbers(List<Lot> lots) {
        StringBuilder sb = new StringBuilder();
        for (Lot lot : lots) {
            if (sb.length() > 0) sb.append(",");
            sb.append(lot.number());
        }
        return sb.toString();
    }

    private static LotSheet sampleSheet() {
        LotSheet sheet = new LotSheet();
        sheet.add(new Lot("C-104", 2, "Hartley Estate", 450));
        sheet.add(new Lot("A-101", 1, "Willow Farm", 300));
        sheet.add(new Lot("A-107", 1, "Willow Farm", 300));
        sheet.add(new Lot("B-201", 1, "Kesler Bros", 900));
        sheet.add(new Lot("C-092", 2, "Pryor Salvage", 1200));
        sheet.add(new Lot("A-113", 3, "Hartley Estate", 150));
        sheet.add(new Lot("B-166", 2, "Kesler Bros", 450));
        return sheet;
    }

    public static void main(String[] args) {
        LotSheet sheet = sampleSheet();

        checkEquals(7, sheet.count(), "seven lots consigned");

        checkEquals("B-201,A-101,A-107,C-092,B-166,C-104,A-113",
                numbers(sheet.ordered()),
                "block order: ring asc, reserve desc, number asc");

        checkEquals("B-201,A-101,A-107", numbers(sheet.ring(1)), "ring 1 slice keeps block order");
        checkEquals("C-092,B-166,C-104", numbers(sheet.ring(2)), "ring 2 slice keeps block order");
        checkEquals("", numbers(sheet.ring(4)), "empty ring is an empty list");

        checkEquals(2100, sheet.reserveTotal(2), "ring 2 reserve total");
        checkEquals(0, sheet.reserveTotal(9), "unknown ring totals zero");

        check(sheet.opener().isPresent(), "a consigned sheet has an opener");
        checkEquals("B-201", sheet.opener().get().number(), "opener is the ring-1 top reserve");

        checkEquals("B-201,A-101,A-107,C-092,B-166,C-104,A-113",
                numbers(sheet.ordered()),
                "ordering is repeatable");

        boolean rejected = false;
        try {
            sheet.add(new Lot("C-104", 3, "Someone Else", 10));
        } catch (IllegalArgumentException e) {
            rejected = true;
        }
        check(rejected, "duplicate lot number is rejected");
        checkEquals(7, sheet.count(), "rejected lot was not added");

        check(new LotSheet().opener().isEmpty(), "empty sheet has no opener");

        if (failures > 0) {
            System.out.println(failures + " check(s) failing");
            System.exit(1);
        }
        System.out.println("all checks passed");
    }
}
