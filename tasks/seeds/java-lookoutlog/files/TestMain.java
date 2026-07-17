import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.ObjectInputStream;
import java.io.ObjectOutputStream;
import java.util.List;

/** Behavior suite for the lookout day book. Do not modify. */
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

    static Object roundTrip(Object o) throws Exception {
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        try (ObjectOutputStream out = new ObjectOutputStream(bos)) {
            out.writeObject(o);
        }
        try (ObjectInputStream in = new ObjectInputStream(new ByteArrayInputStream(bos.toByteArray()))) {
            return in.readObject();
        }
    }

    public static void main(String[] args) throws Exception {
        // radio protocol per severity — exact call order
        checkEq(List.of("ring-brigade", "radio-neighbours", "day-log"),
                LookoutLog.actionsFor(LookoutLog.Severity.FIRE), "FIRE protocol");
        checkEq(List.of("radio-neighbours", "day-log"),
                LookoutLog.actionsFor(LookoutLog.Severity.SMOKE), "SMOKE protocol");
        checkEq(List.of("day-log"),
                LookoutLog.actionsFor(LookoutLog.Severity.HAZE), "HAZE protocol");
        checkEq(List.of("day-log"),
                LookoutLog.actionsFor(LookoutLog.Severity.ROUTINE), "ROUTINE protocol");

        // a full shift on Bald Knob tower
        LookoutLog log = new LookoutLog("Bald Knob");
        boolean threw = false;
        try {
            log.record(LookoutLog.Severity.HAZE, 40, "valley haze", List.of());
        } catch (IllegalStateException e) {
            threw = true;
            checkEq("no active shift", e.getMessage(), "record before sign-in message");
        }
        check(threw, "record before sign-in must be rejected");

        log.signIn();
        log.record(LookoutLog.Severity.ROUTINE, 120, "dust from the quarry", List.of("quarry"));
        log.record(LookoutLog.Severity.SMOKE, 285, "grey column past the ridge", List.of("ridge", "column"));
        SightingRecord fire = log.record(LookoutLog.Severity.FIRE, 290, "open flame confirmed", List.of("ridge"));
        checkEq(3, log.entries().size(), "three sightings booked");
        checkEq("Bald Knob", fire.tower(), "record carries the tower name");
        checkEq(List.of("ridge"), fire.tags(), "record carries its tags");

        // closing the shift archives one summary and wipes the board
        checkEq(3, log.closeShift(), "closeShift reports how many entries flushed");
        checkEq(0, log.entries().size(), "board wiped after close");
        checkEq(1, log.summaries().size(), "one summary archived");
        ShiftSummary s = log.summaries().get(0);
        checkEq("Bald Knob", s.tower(), "summary tower");
        checkEq(3, s.sightings(), "summary sighting count");
        checkEq(2, s.alerts(), "SMOKE + FIRE count as alerts");

        // closing again without a new sign-in is an error and archives nothing
        threw = false;
        try {
            log.closeShift();
        } catch (IllegalStateException e) {
            threw = true;
            checkEq("no active shift", e.getMessage(), "double close message");
        }
        check(threw, "closing a closed shift must be rejected");
        checkEq(1, log.summaries().size(), "no extra summary from the failed close");

        // the archive format: records survive serialization field-for-field
        SightingRecord rec = new SightingRecord("Windy Point", 15, "smoke over O'Leary's block",
                List.of("private-land", "follow-up"));
        SightingRecord back = (SightingRecord) roundTrip(rec);
        checkEq("Windy Point", back.tower(), "round-trip tower");
        checkEq(15, back.bearing(), "round-trip bearing");
        checkEq("smoke over O'Leary's block", back.note(), "round-trip note");
        checkEq(List.of("private-land", "follow-up"), back.tags(), "round-trip tags");

        ShiftSummary sum = (ShiftSummary) roundTrip(new ShiftSummary("Windy Point", 7, 1));
        checkEq("Windy Point", sum.tower(), "round-trip summary tower");
        checkEq(7, sum.sightings(), "round-trip summary sightings");
        checkEq(1, sum.alerts(), "round-trip summary alerts");

        System.out.println("all " + passed + " checks PASS");
    }
}
