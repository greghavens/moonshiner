import java.util.ArrayList;
import java.util.List;

/**
 * Batch normalizer for controller uploads. Every door controller uploads
 * its night of swipes as wire stamps; the campus ingest job runs one
 * worker per controller and merges the normalized batches afterwards.
 */
public final class SwipeLog {
    private SwipeLog() {}

    /** One upload's stamps -> epoch millis, order preserved. */
    public static long[] normalizeBatch(List<String> stamps) {
        long[] epochs = new long[stamps.size()];
        for (int i = 0; i < stamps.size(); i++) {
            epochs[i] = StampCodec.parseEpochMillis(stamps.get(i));
        }
        return epochs;
    }

    /**
     * Audit-report lines for one normalized batch: "&lt;epoch&gt;  &lt;wire stamp&gt;",
     * the stamp round-tripped from the epoch so auditors can eyeball both.
     */
    public static List<String> auditLines(long[] epochs) {
        List<String> lines = new ArrayList<>(epochs.length);
        for (long e : epochs) {
            lines.add(e + "  " + StampCodec.formatEpochMillis(e));
        }
        return lines;
    }
}
