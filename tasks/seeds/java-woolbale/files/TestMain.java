import java.util.List;

/** Behavior suite for the intake registry. Do not modify. */
public class TestMain {
    static int passed = 0;

    static void check(boolean cond, String what) {
        if (!cond) {
            System.out.println("FAIL: " + what);
            System.exit(1);
        }
        passed++;
    }

    static void checkEq(Object expected, Object actual, String what) {
        check(expected.equals(actual), what + " — expected " + expected + ", got " + actual);
    }

    static List<String> ids(List<Bale> bales) {
        List<String> out = new java.util.ArrayList<>();
        for (Bale b : bales) {
            out.add(b.id());
        }
        return out;
    }

    public static void main(String[] args) {
        BaleRegistry reg = new BaleRegistry();
        reg.intake(new Bale("W042", "O'Hara", 187.5, 21));
        reg.intake(new Bale("W043", "Ferris", 142.25, 19));
        reg.intake(new Bale("W044", "O'Hara", 190.0, 22));
        reg.intake(new Bale("W045", "Blay", 187.5, 20));
        reg.intake(new Bale("W046", "Ferris", 201.75, 18));
        reg.intake(new Bale("W047", "O'Hara", 142.25, 21));

        // grouping and intake order inside a lot
        checkEq(List.of("W042", "W044", "W047"), ids(reg.lot("O'Hara")), "O'Hara lot order");
        checkEq(List.of("W043", "W046"), ids(reg.lot("Ferris")), "Ferris lot order");
        checkEq(List.of("W045"), ids(reg.lot("Blay")), "Blay lot");
        check(reg.lot("Nobody").isEmpty(), "unknown grower gives empty lot");
        checkEq(3, reg.growerCount(), "grower count");

        // lot() hands back a copy — mutating it must not touch the registry
        List<Bale> copy = reg.lot("Blay");
        copy.clear();
        checkEq(List.of("W045"), ids(reg.lot("Blay")), "lot() returns a defensive copy");

        // pen totals
        checkEq(519.75, BaleRegistry.totalKg(reg.lot("O'Hara")), "O'Hara total kg");
        checkEq(344.0, BaleRegistry.totalKg(reg.lot("Ferris")), "Ferris total kg");
        checkEq(0.0, BaleRegistry.totalKg(reg.lot("Nobody")), "empty pen total kg");

        // shed-wide report: heaviest first, W042/W045 tie broken by ticket id
        checkEq(List.of("W046", "W044", "W042", "W045", "W043", "W047"),
                ids(reg.heaviestFirst()), "heaviest-first order with tie-break");

        // truck manifest flattening keeps pen order
        checkEq(List.of("W042", "W044", "W047", "W043", "W046"),
                ids(BaleRegistry.loadOrder(reg.lot("O'Hara"), reg.lot("Ferris"))),
                "two-pen load order");
        checkEq(List.of("W045"), ids(BaleRegistry.loadOrder(reg.lot("Blay"))),
                "single-pen load order");
        check(BaleRegistry.loadOrder().isEmpty(), "no pens, empty manifest");

        // weighbridge tags
        checkEq(List.of("1:W042", "2:W043", "3:W044", "4:W045", "5:W046", "6:W047"),
                reg.intakeTags(), "intake tags in weighbridge order");

        System.out.println("all " + passed + " checks PASS");
    }
}
