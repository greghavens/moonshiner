import java.io.StringWriter;
import java.util.ArrayList;
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

    private static void checkClose(double expected, double actual, String what) {
        check(Math.abs(expected - actual) < 1e-9, what + " (expected " + expected + ", got " + actual + ")");
    }

    private static String labels(List<Site> sites) {
        StringBuilder sb = new StringBuilder();
        for (Site s : sites) {
            if (sb.length() > 0) sb.append(",");
            sb.append(s.label());
        }
        return sb.toString();
    }

    public static void main(String[] args) throws Exception {
        checkClose(66.0, new TentSite("T9", 22.0).total(3), "tent total is rate times nights");
        checkClose(230.0, new CabinSite("C9", 95.0, 40.0).total(2), "cabin total adds one turnover fee");
        checkClose(96.0, new RvSite("R9", 48.0).total(2), "short rv stay has no discount");
        checkClose(302.4, new RvSite("R9", 48.0).total(7), "weekly rv stay gets ten percent off");

        CampBoard board = CampBoard.demo();
        checkEquals(4, board.count(), "demo board has four sites");
        checkEquals("== Pinewood Hollow site board ==", board.banner(), "banner names the park");

        boolean rejected = false;
        try {
            board.register(new TentSite("T1", 30.0));
        } catch (IllegalArgumentException e) {
            rejected = true;
        }
        check(rejected, "duplicate label is rejected");
        checkEquals(4, board.count(), "rejected site was not added");

        List<Site> tents = new ArrayList<>();
        board.copyHookupSites("none", tents);
        checkEquals("T1,T2", labels(tents), "hookup filter keeps registration order");

        List<Site> full = new ArrayList<>();
        board.copyHookupSites("full", full);
        checkEquals("C1", labels(full), "cabins are the only full-hookup sites");

        List<Site> all = new ArrayList<>();
        board.copyHookupSites("none", all);
        board.copyHookupSites("partial", all);
        board.copyHookupSites("full", all);
        checkClose(22.0, CampBoard.cheapestRate(all), "cheapest rate across the demo board");
        checkClose(0.0, CampBoard.cheapestRate(new ArrayList<Site>()), "empty candidate list rates 0.0");

        StringWriter roster = new StringWriter();
        board.writeRoster(roster);
        checkEquals("T1  none\nT2  none\nR4  partial\nC1  full\n", roster.toString(),
                "roster lists every site in registration order");

        checkEquals("fire ring only", board.amenities("none"), "amenities for tent sites");
        checkEquals("power pedestal", board.amenities("partial"), "amenities for rv sites");
        checkEquals("power, water, sewer", board.amenities("full"), "amenities for cabins");
        checkEquals("unlisted", board.amenities("boat-in"), "unknown hookup class is unlisted");

        if (failures > 0) {
            System.out.println(failures + " check(s) failing");
            System.exit(1);
        }
        System.out.println("all checks passed");
    }
}
