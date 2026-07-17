import java.util.ArrayList;
import java.util.List;

/**
 * Pre-broadcast compliance pass over a block's setlist. Pulls anything the
 * slot's rules do not allow — explicit lyrics outside safe harbor, or a
 * track longer than the slot's per-track cap — and reports what was pulled
 * for the program log.
 */
public final class ComplianceSweep {
    private final int maxTrackSeconds;
    private final boolean explicitAllowed;

    public ComplianceSweep(int maxTrackSeconds, boolean explicitAllowed) {
        if (maxTrackSeconds <= 0) {
            throw new IllegalArgumentException("maxTrackSeconds must be positive");
        }
        this.maxTrackSeconds = maxTrackSeconds;
        this.explicitAllowed = explicitAllowed;
    }

    private boolean violates(Track t) {
        return (t.explicitLyrics() && !explicitAllowed) || t.seconds() > maxTrackSeconds;
    }

    /**
     * Removes non-compliant tracks from the setlist in place, preserving
     * the running order of everything that stays. Returns the pulled titles
     * in their original order for the program log.
     */
    public List<String> enforce(List<Track> setlist) {
        List<String> pulled = new ArrayList<>();
        for (Track t : setlist) {
            if (violates(t)) {
                setlist.remove(t);
                pulled.add(t.title());
            }
        }
        return pulled;
    }
}
