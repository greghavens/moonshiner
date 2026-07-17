import java.io.Serializable;
import java.util.ArrayList;
import java.util.List;

/** One radioed-in sighting, exactly as it gets archived at end of shift. */
public final class SightingRecord implements Serializable {

    private final String tower;
    private final int bearing;          // degrees from the tower
    private final String note;
    private final List<String> tags;

    public SightingRecord(String tower, int bearing, String note, List<String> tags) {
        this.tower = tower;
        this.bearing = bearing;
        this.note = note;
        this.tags = new ArrayList<>(tags);
    }

    public String tower() { return tower; }
    public int bearing() { return bearing; }
    public String note() { return note; }
    public List<String> tags() { return new ArrayList<>(tags); }
}
