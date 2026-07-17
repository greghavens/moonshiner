import java.io.Serializable;

/** End-of-shift roll-up archived to the district office. */
public final class ShiftSummary implements Serializable {

    private final String tower;
    private final int sightings;
    private final int alerts;

    public ShiftSummary(String tower, int sightings, int alerts) {
        this.tower = tower;
        this.sightings = sightings;
        this.alerts = alerts;
    }

    public String tower() { return tower; }
    public int sightings() { return sightings; }
    public int alerts() { return alerts; }
}
