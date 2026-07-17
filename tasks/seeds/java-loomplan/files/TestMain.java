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

    public static void main(String[] args) {
        LoomPlan plan = new LoomPlan();
        plan.addSection("left selvedge", 12);
        plan.addSection("body", 240);
        plan.addSection("stripe", 36);
        plan.addSection("right selvedge", 12);

        checkEquals(300, plan.totalEnds(), "total warp ends across all sections");
        checkEquals(4, plan.draftCount(), "one draft per section");
        checkEquals(List.of("left selvedge", "body", "stripe", "right selvedge"),
                plan.beamingOrder(), "beaming order matches the order sections were added");

        plan.assignColorway("body", "indigo");
        plan.assignColorway("stripe", "madder");
        checkEquals("indigo", plan.colorwayFor("body"), "assigned colorway comes back");
        checkEquals("madder", plan.colorwayFor("stripe"), "second colorway comes back");
        checkEquals("natural", plan.colorwayFor("left selvedge"), "unassigned section weaves natural");

        plan.assignColorway("body", "walnut");
        checkEquals("walnut", plan.colorwayFor("body"), "reassignment replaces the colorway");

        checkEquals(List.of(List.of("1", "2", "3", "4"), List.of("4", "3", "2", "1")),
                plan.threadingBlocks(), "point twill threading blocks");

        boolean rejected = false;
        try {
            plan.addSection("ghost", 0);
        } catch (IllegalArgumentException e) {
            rejected = true;
        }
        check(rejected, "zero-end section is rejected");
        checkEquals(300, plan.totalEnds(), "rejected section did not change the plan");

        LoomPlan empty = new LoomPlan();
        checkEquals(0, empty.totalEnds(), "empty plan has zero ends");
        checkEquals(0, empty.draftCount(), "empty plan has zero drafts");

        if (failures > 0) {
            System.out.println(failures + " check(s) failing");
            System.exit(1);
        }
        System.out.println("all checks passed");
    }
}
