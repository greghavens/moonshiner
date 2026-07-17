import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * Merge target for the weekly roundup. Spotter reports come in one at a
 * time; the log collapses duplicate reports of the same sighting and keeps
 * a per-sighting report tally for the newsletter's "confirmed by N
 * observers" line.
 */
public final class SightingLog {
    private final Set<Sighting> unique = new HashSet<>();
    private final Map<Sighting, Integer> reportTally = new HashMap<>();
    private int totalReports = 0;

    /**
     * Records one spotter report. Returns true when this is a new sighting,
     * false when it merely confirms one already in the log.
     */
    public boolean record(Sighting s) {
        totalReports++;
        reportTally.merge(s, 1, Integer::sum);
        return unique.add(s);
    }

    /** Has this sighting (from anyone) already been logged this week? */
    public boolean alreadyRecorded(Sighting s) {
        return unique.contains(s);
    }

    /** How many spotters reported this sighting. Zero when it is unknown. */
    public int confirmations(Sighting s) {
        return reportTally.getOrDefault(s, 0);
    }

    /** Number of distinct sightings after merging duplicate reports. */
    public int uniqueCount() {
        return unique.size();
    }

    /** Raw report count, duplicates included. */
    public int totalReports() {
        return totalReports;
    }

    /** Distinct species codes seen this week, alphabetical. */
    public List<String> speciesIndex() {
        Set<String> codes = new TreeSet<>();
        for (Sighting s : unique) {
            codes.add(s.speciesCode());
        }
        return new ArrayList<>(codes);
    }
}
