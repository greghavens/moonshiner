import java.util.ArrayList;
import java.util.List;

/** Day book for one fire-lookout tower: sightings in, shift summaries out. */
public final class LookoutLog {

    public enum Severity { ROUTINE, HAZE, SMOKE, FIRE }

    private final String tower;
    private final List<SightingRecord> entries = new ArrayList<>();
    private final List<ShiftSummary> summaries = new ArrayList<>();
    private int alerts = 0;
    private boolean onDuty = false;

    public LookoutLog(String tower) {
        this.tower = tower;
    }

    public void signIn() {
        onDuty = true;
    }

    /** Radio protocol for a report of the given severity, in call order. */
    public static List<String> actionsFor(Severity sev) {
        List<String> actions = new ArrayList<>();
        switch (sev) {
            case FIRE:
                actions.add("ring-brigade");
                // a confirmed fire also does everything a smoke report does
            case SMOKE:
                actions.add("radio-neighbours");
                // ...and every report of any kind lands in the day log
            case HAZE:
            case ROUTINE:
                actions.add("day-log");
                break;
        }
        return actions;
    }

    /** Book a sighting; SMOKE and FIRE reports count as alerts. */
    public SightingRecord record(Severity sev, int bearing, String note, List<String> tags) {
        if (!onDuty) {
            throw new IllegalStateException("no active shift");
        }
        SightingRecord rec = new SightingRecord(tower, bearing, note, tags);
        entries.add(rec);
        if (sev == Severity.SMOKE || sev == Severity.FIRE) {
            alerts++;
        }
        return rec;
    }

    /** Sightings booked so far this shift, oldest first (a copy). */
    public List<SightingRecord> entries() {
        return new ArrayList<>(entries);
    }

    /** Summaries archived so far, oldest first (a copy). */
    public List<ShiftSummary> summaries() {
        return new ArrayList<>(summaries);
    }

    /** Close the shift: archive a summary, wipe the board, report how many flushed. */
    public int closeShift() {
        int flushed = entries.size();
        try {
            if (!onDuty) {
                throw new IllegalStateException("no active shift");
            }
            summaries.add(new ShiftSummary(tower, flushed, alerts));
            return flushed;
        } finally {
            onDuty = false;
            entries.clear();
            alerts = 0;
            return flushed;
        }
    }
}
