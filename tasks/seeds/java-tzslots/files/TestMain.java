import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Objects;

/**
 * Acceptance tests for the cross-timezone meeting-slot finder.
 * All dates are fixed 2026 constants; zone rules come from the JDK tzdata.
 * Run: java TestMain.java
 */
public final class TestMain {
    private static int passed = 0;
    private static int failed = 0;

    interface Body { void run() throws Exception; }

    private static void test(String name, Body body) {
        try {
            body.run();
            passed++;
            System.out.println("PASS " + name);
        } catch (Throwable t) {
            failed++;
            System.out.println("FAIL " + name + ": " + t);
        }
    }

    private static void eq(String what, Object expected, Object actual) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(what + ": expected <" + expected + "> got <" + actual + ">");
        }
    }

    private static void yes(String what, boolean cond) {
        if (!cond) throw new AssertionError(what);
    }

    private static <X extends Throwable> X thrown(Class<X> type, Body body) {
        try {
            body.run();
        } catch (Throwable t) {
            if (type.isInstance(t)) return type.cast(t);
            throw new AssertionError("expected " + type.getSimpleName() + " but got " + t, t);
        }
        throw new AssertionError("expected " + type.getSimpleName() + " but nothing was thrown");
    }

    private static final ZoneId NY = ZoneId.of("America/New_York");
    private static final ZoneId BERLIN = ZoneId.of("Europe/Berlin");
    private static final ZoneId KOLKATA = ZoneId.of("Asia/Kolkata");
    private static final ZoneId UTC = ZoneId.of("UTC");

    private static TimeRange tr(int sh, int sm, int eh, int em) {
        return new TimeRange(LocalTime.of(sh, sm), LocalTime.of(eh, em));
    }

    private static Participant p(String name, ZoneId zone, TimeRange workday, TimeRange... busy) {
        return new Participant(name, zone, workday, List.of(busy));
    }

    private static void pinSlot(String what, Slot s, String startUtc, String endUtc, ZoneId displayZone) {
        eq(what + " start instant", Instant.parse(startUtc), s.start().toInstant());
        eq(what + " end instant", Instant.parse(endUtc), s.end().toInstant());
        eq(what + " start zone", displayZone, s.start().getZone());
        eq(what + " end zone", displayZone, s.end().getZone());
    }

    public static void main(String[] args) {

        test("basic_two_zone_overlap_in_display_zone", () -> {
            List<Slot> slots = MeetingSlots.find(
                    List.of(p("Nia", NY, tr(9, 0, 17, 0)),
                            p("Ben", BERLIN, tr(9, 0, 17, 0))),
                    LocalDate.of(2026, 2, 10), Duration.ofHours(1), BERLIN);
            eq("one slot", 1, slots.size());
            pinSlot("slot", slots.get(0), "2026-02-10T14:00:00Z", "2026-02-10T16:00:00Z", BERLIN);
            eq("berlin wall-clock start", LocalTime.of(15, 0), slots.get(0).start().toLocalTime());
            eq("berlin offset", ZoneOffset.ofHours(1), slots.get(0).start().getOffset());
            eq("length", Duration.ofHours(2), slots.get(0).length());
        });

        test("busy_block_splits_the_window", () -> {
            List<Slot> slots = MeetingSlots.find(
                    List.of(p("Nia", NY, tr(9, 0, 17, 0)),
                            p("Ben", BERLIN, tr(9, 0, 17, 0), tr(15, 30, 16, 0))),
                    LocalDate.of(2026, 2, 10), Duration.ofMinutes(30), UTC);
            eq("two slots", 2, slots.size());
            pinSlot("first", slots.get(0), "2026-02-10T14:00:00Z", "2026-02-10T14:30:00Z", UTC);
            pinSlot("second", slots.get(1), "2026-02-10T15:00:00Z", "2026-02-10T16:00:00Z", UTC);
            yes("sorted ascending", slots.get(0).start().toInstant()
                    .isBefore(slots.get(1).start().toInstant()));
            thrown(UnsupportedOperationException.class,
                    () -> slots.add(slots.get(0)));
        });

        test("min_length_is_inclusive_and_filters_shorter_gaps", () -> {
            List<Participant> people = List.of(
                    p("Nia", NY, tr(9, 0, 17, 0)),
                    p("Ben", BERLIN, tr(9, 0, 17, 0), tr(15, 30, 16, 0)));
            List<Slot> atLeast45 = MeetingSlots.find(people,
                    LocalDate.of(2026, 2, 10), Duration.ofMinutes(45), UTC);
            eq("only the hour-long slot survives", 1, atLeast45.size());
            pinSlot("slot", atLeast45.get(0), "2026-02-10T15:00:00Z", "2026-02-10T16:00:00Z", UTC);
            List<Slot> exactly30 = MeetingSlots.find(people,
                    LocalDate.of(2026, 2, 10), Duration.ofMinutes(30), UTC);
            eq("a slot exactly minLength long is kept", 2, exactly30.size());
        });

        test("dst_misalignment_changes_the_overlap_through_the_year", () -> {
            List<Participant> people = List.of(
                    p("Nia", NY, tr(9, 0, 17, 0)),
                    p("Ben", BERLIN, tr(9, 0, 17, 0)));
            // Both on standard time: 6h apart -> 2h overlap.
            List<Slot> feb = MeetingSlots.find(people, LocalDate.of(2026, 2, 10),
                    Duration.ofHours(1), UTC);
            pinSlot("february", feb.get(0), "2026-02-10T14:00:00Z", "2026-02-10T16:00:00Z", UTC);
            // NY sprang forward 2026-03-08, Berlin not until 03-29: 5h apart -> 3h overlap.
            List<Slot> mismatch = MeetingSlots.find(people, LocalDate.of(2026, 3, 10),
                    Duration.ofHours(1), UTC);
            pinSlot("march mismatch", mismatch.get(0), "2026-03-10T13:00:00Z", "2026-03-10T16:00:00Z", UTC);
            eq("march mismatch length", Duration.ofHours(3), mismatch.get(0).length());
            // Both on summer time: back to 6h apart -> 2h overlap.
            List<Slot> apr = MeetingSlots.find(people, LocalDate.of(2026, 4, 7),
                    Duration.ofHours(1), UTC);
            pinSlot("april", apr.get(0), "2026-04-07T13:00:00Z", "2026-04-07T15:00:00Z", UTC);
            // Berlin fell back 2026-10-25, NY not until 11-01: 5h apart again.
            List<Slot> oct = MeetingSlots.find(people, LocalDate.of(2026, 10, 28),
                    Duration.ofHours(1), UTC);
            pinSlot("october mismatch", oct.get(0), "2026-10-28T13:00:00Z", "2026-10-28T16:00:00Z", UTC);
        });

        test("spring_forward_gap_shifts_a_window_start_that_does_not_exist", () -> {
            // 2026-03-08 02:30 does not exist in New York; it resolves to 03:30 EDT.
            List<Slot> slots = MeetingSlots.find(
                    List.of(p("Mia", NY, tr(2, 30, 10, 0))),
                    LocalDate.of(2026, 3, 8), Duration.ofHours(1), NY);
            eq("one slot", 1, slots.size());
            pinSlot("slot", slots.get(0), "2026-03-08T07:30:00Z", "2026-03-08T14:00:00Z", NY);
            eq("local start shifted into existence", LocalTime.of(3, 30),
                    slots.get(0).start().toLocalTime());
            eq("EDT offset after the jump", ZoneOffset.ofHours(-4), slots.get(0).start().getOffset());
            eq("length", Duration.ofHours(6).plusMinutes(30), slots.get(0).length());
        });

        test("busy_block_swallowed_by_the_gap_is_ignored_and_slot_spans_the_transition", () -> {
            // Zoe's 02:00-03:00 sync lands entirely inside the nonexistent hour.
            List<Slot> slots = MeetingSlots.find(
                    List.of(p("Zoe", NY, tr(1, 0, 5, 0), tr(2, 0, 3, 0)),
                            p("Ravi", KOLKATA, tr(12, 0, 20, 0))),
                    LocalDate.of(2026, 3, 8), Duration.ofHours(1), NY);
            eq("one slot", 1, slots.size());
            Slot s = slots.get(0);
            pinSlot("slot", s, "2026-03-08T06:30:00Z", "2026-03-08T09:00:00Z", NY);
            eq("starts on EST", ZoneOffset.ofHours(-5), s.start().getOffset());
            eq("ends on EDT", ZoneOffset.ofHours(-4), s.end().getOffset());
            eq("start wall clock", LocalTime.of(1, 30), s.start().toLocalTime());
            eq("end wall clock", LocalTime.of(5, 0), s.end().toLocalTime());
            eq("real elapsed length, not wall-clock math",
                    Duration.ofHours(2).plusMinutes(30), s.length());
        });

        test("fall_back_overlap_resolves_to_the_earlier_offset", () -> {
            // 2026-11-01 01:30 happens twice in New York; the first (EDT) wins.
            List<Slot> slots = MeetingSlots.find(
                    List.of(p("Wes", NY, tr(1, 30, 9, 0)),
                            p("Britta", BERLIN, tr(8, 0, 16, 0))),
                    LocalDate.of(2026, 11, 1), Duration.ofHours(1), UTC);
            eq("one slot", 1, slots.size());
            pinSlot("slot", slots.get(0), "2026-11-01T07:00:00Z", "2026-11-01T14:00:00Z", UTC);
            eq("seven real hours on a 25-hour day", Duration.ofHours(7), slots.get(0).length());
        });

        test("berlin_fall_back_busy_hour_covers_two_real_hours", () -> {
            // 2026-10-25: Berlin repeats 02:00-03:00. A 02:30-03:30 busy block
            // resolves 02:30 to the earlier (+02:00) pass -> 00:30Z..02:30Z, 2h real.
            List<Slot> slots = MeetingSlots.find(
                    List.of(p("Ben", BERLIN, tr(1, 0, 8, 0), tr(2, 30, 3, 30))),
                    LocalDate.of(2026, 10, 25), Duration.ofHours(1), UTC);
            eq("two slots", 2, slots.size());
            pinSlot("before the busy block", slots.get(0),
                    "2026-10-24T23:00:00Z", "2026-10-25T00:30:00Z", UTC);
            eq("window opens the previous UTC day", LocalDate.of(2026, 10, 24),
                    slots.get(0).start().toLocalDate());
            pinSlot("after the busy block", slots.get(1),
                    "2026-10-25T02:30:00Z", "2026-10-25T07:00:00Z", UTC);
            eq("first length", Duration.ofMinutes(90), slots.get(0).length());
            eq("second length", Duration.ofHours(4).plusMinutes(30), slots.get(1).length());
        });

        test("three_zones_with_unsorted_overlapping_busy_blocks", () -> {
            List<Slot> slots = MeetingSlots.find(
                    List.of(p("Nia", NY, tr(9, 0, 17, 0)),
                            p("Ben", BERLIN, tr(9, 0, 17, 0),
                                    tr(16, 0, 16, 45), tr(15, 30, 16, 15)),
                            p("Ravi", KOLKATA, tr(18, 0, 23, 0))),
                    LocalDate.of(2026, 2, 10), Duration.ofMinutes(30), KOLKATA);
            eq("only the half-hour before Ben's merged blocks survives", 1, slots.size());
            Slot s = slots.get(0);
            pinSlot("slot", s, "2026-02-10T14:00:00Z", "2026-02-10T14:30:00Z", KOLKATA);
            eq("kolkata wall clock", LocalTime.of(19, 30), s.start().toLocalTime());
            eq("half-hour offset zone", ZoneOffset.ofHoursMinutes(5, 30), s.start().getOffset());
        });

        test("disjoint_working_hours_produce_no_slots", () -> {
            List<Slot> slots = MeetingSlots.find(
                    List.of(p("Nia", NY, tr(9, 0, 12, 0)),
                            p("Ravi", KOLKATA, tr(9, 0, 12, 0))),
                    LocalDate.of(2026, 2, 10), Duration.ofMinutes(15), UTC);
            eq("empty result", List.of(), slots);
        });

        test("busy_covering_the_whole_window_leaves_nothing", () -> {
            List<Slot> slots = MeetingSlots.find(
                    List.of(p("Ben", BERLIN, tr(10, 0, 12, 0), tr(9, 0, 13, 0))),
                    LocalDate.of(2026, 2, 10), Duration.ofMinutes(15), BERLIN);
            eq("empty result", List.of(), slots);
        });

        test("slot_record_length_and_equality", () -> {
            ZonedDateTime start = ZonedDateTime.of(LocalDate.of(2026, 2, 10),
                    LocalTime.of(15, 0), BERLIN);
            Slot a = new Slot(start, start.plusHours(2));
            eq("length", Duration.ofHours(2), a.length());
            eq("value equality", new Slot(start, start.plusHours(2)), a);
        });

        test("find_argument_validation", () -> {
            List<Participant> ok = List.of(p("Nia", NY, tr(9, 0, 17, 0)));
            eq("null people", "people must not be empty",
                    thrown(IllegalArgumentException.class, () -> MeetingSlots.find(
                            null, LocalDate.of(2026, 2, 10), Duration.ofHours(1), UTC)).getMessage());
            eq("empty people", "people must not be empty",
                    thrown(IllegalArgumentException.class, () -> MeetingSlots.find(
                            List.of(), LocalDate.of(2026, 2, 10), Duration.ofHours(1), UTC)).getMessage());
            eq("null participant", "people must not contain null",
                    thrown(IllegalArgumentException.class, () -> MeetingSlots.find(
                            Arrays.asList(ok.get(0), null), LocalDate.of(2026, 2, 10),
                            Duration.ofHours(1), UTC)).getMessage());
            eq("null date", "date must not be null",
                    thrown(IllegalArgumentException.class, () -> MeetingSlots.find(
                            ok, null, Duration.ofHours(1), UTC)).getMessage());
            eq("null minLength", "minLength must be positive",
                    thrown(IllegalArgumentException.class, () -> MeetingSlots.find(
                            ok, LocalDate.of(2026, 2, 10), null, UTC)).getMessage());
            eq("zero minLength", "minLength must be positive",
                    thrown(IllegalArgumentException.class, () -> MeetingSlots.find(
                            ok, LocalDate.of(2026, 2, 10), Duration.ZERO, UTC)).getMessage());
            eq("negative minLength", "minLength must be positive",
                    thrown(IllegalArgumentException.class, () -> MeetingSlots.find(
                            ok, LocalDate.of(2026, 2, 10), Duration.ofMinutes(-5), UTC)).getMessage());
            eq("null displayZone", "displayZone must not be null",
                    thrown(IllegalArgumentException.class, () -> MeetingSlots.find(
                            ok, LocalDate.of(2026, 2, 10), Duration.ofHours(1), null)).getMessage());
        });

        test("time_range_validation", () -> {
            eq("null start", "start must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> new TimeRange(null, LocalTime.of(10, 0))).getMessage());
            eq("null end", "end must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> new TimeRange(LocalTime.of(10, 0), null)).getMessage());
            eq("inverted", "start must be before end",
                    thrown(IllegalArgumentException.class,
                            () -> new TimeRange(LocalTime.of(10, 0), LocalTime.of(10, 0))).getMessage());
        });

        test("participant_validation_and_defensive_busy_copy", () -> {
            eq("null name", "name must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> new Participant(null, NY, tr(9, 0, 17, 0), List.of())).getMessage());
            eq("null zone", "zone must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> new Participant("Nia", null, tr(9, 0, 17, 0), List.of())).getMessage());
            eq("null workday", "workday must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> new Participant("Nia", NY, null, List.of())).getMessage());
            eq("null busy", "busy must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> new Participant("Nia", NY, tr(9, 0, 17, 0), null)).getMessage());
            eq("null busy element", "busy must not contain null",
                    thrown(IllegalArgumentException.class,
                            () -> new Participant("Nia", NY, tr(9, 0, 17, 0),
                                    Arrays.asList(tr(12, 0, 13, 0), null))).getMessage());
            List<TimeRange> source = new ArrayList<>(List.of(tr(12, 0, 13, 0)));
            Participant nia = new Participant("Nia", NY, tr(9, 0, 17, 0), source);
            source.add(tr(14, 0, 15, 0));
            eq("caller mutation does not leak in", List.of(tr(12, 0, 13, 0)), nia.busy());
            thrown(UnsupportedOperationException.class, () -> nia.busy().add(tr(1, 0, 2, 0)));
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
