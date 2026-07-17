import java.io.Writer;
import java.util.ArrayList;
import java.util.List;

public class CampBoard {
    private static final String NONE = "none";
    private static final String PARTIAL = "partial";
    private static final String FULL = Hookups.normalize("FULL");

    private final String parkName;
    private final List<Site> sites = new ArrayList<>();

    public CampBoard(String parkName) {
        this.parkName = parkName;
    }

    public void register(Site site) {
        for (Site s : sites) {
            if (s.label().equals(site.label())) {
                throw new IllegalArgumentException("duplicate site " + site.label());
            }
        }
        sites.add(site);
    }

    public int count() {
        return sites.size();
    }

    public static String banner() {
        return "== " + parkName + " site board ==";
    }

    /** Copies every site of the given hookup class into the caller's list, in registration order. */
    public void copyHookupSites(String hookupClass, List<? extends Site> out) {
        for (Site s : sites) {
            if (s.hookupClass().equals(hookupClass)) {
                out.add(s);
            }
        }
    }

    /** Lowest nightly base rate among the candidates; 0.0 when the list is empty. */
    public static double cheapestRate(List<? super Site> candidates) {
        double best = 0.0;
        boolean found = false;
        for (Site s : candidates) {
            if (!found || s.baseRate() < best) {
                best = s.baseRate();
                found = true;
            }
        }
        return best;
    }

    /** One line per site, registration order: "LABEL  hookupclass". */
    public void writeRoster(Writer out) {
        sites.forEach(s -> out.append(s.label() + "  " + s.hookupClass() + "\n"));
    }

    public String amenities(String hookupClass) {
        switch (hookupClass) {
            case NONE:
                return "fire ring only";
            case PARTIAL:
                return "power pedestal";
            case FULL:
                return "power, water, sewer";
            default:
                return "unlisted";
        }
    }

    /** The starter board we hand to seasonal staff for training. */
    public static CampBoard demo() {
        CampBoard board = new CampBoard("Pinewood Hollow");
        Site[] starters = {
                new TentSite("T1", 22.0),
                new TentSite("T2", 22.0),
                new RvSite("R4", 48.0),
                new CabinSite("C1", 95.0, 40.0),
        };
        for (Site s : starters) {
            register(s);
        }
        return board;
    }
}
