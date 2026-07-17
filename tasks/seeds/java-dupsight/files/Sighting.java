import java.time.LocalDate;
import java.util.Objects;

/**
 * One field sighting reported by a club spotter.
 *
 * Two reports describe the same sighting when the species code, the site,
 * and the date all match — the club logs a sighting once no matter how many
 * spotters phoned it in. The reporting spotter is attribution, not identity.
 */
public final class Sighting {
    private final String speciesCode;   // four-letter banding code, e.g. "PIWO"
    private final String site;          // e.g. "Cedar Bog boardwalk"
    private final LocalDate date;
    private final String spotter;       // who called it in (not part of identity)

    public Sighting(String speciesCode, String site, LocalDate date, String spotter) {
        this.speciesCode = Objects.requireNonNull(speciesCode, "speciesCode");
        this.site = Objects.requireNonNull(site, "site");
        this.date = Objects.requireNonNull(date, "date");
        this.spotter = Objects.requireNonNull(spotter, "spotter");
    }

    public String speciesCode() { return speciesCode; }
    public String site() { return site; }
    public LocalDate date() { return date; }
    public String spotter() { return spotter; }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (!(o instanceof Sighting)) return false;
        Sighting other = (Sighting) o;
        return speciesCode.equals(other.speciesCode)
                && site.equals(other.site)
                && date.equals(other.date);
    }

    @Override
    public String toString() {
        return speciesCode + " @ " + site + " on " + date + " (per " + spotter + ")";
    }
}
