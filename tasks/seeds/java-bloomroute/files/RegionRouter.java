import java.util.Comparator;
import java.util.List;
import java.util.TreeMap;

/**
 * Maps delivery zones to the partner shop that fulfills them. Zone codes
 * look like "NE-11": two-letter region, dash, district number.
 */
public final class RegionRouter {

    // Keyed by zone; grouped by the two-letter region so the coverage
    // report reads region-by-region.
    private final TreeMap<String, String> shopByZone =
            new TreeMap<>(Comparator.comparing(zone -> zone.substring(0, 2)));

    public void register(String zone, String shop) {
        shopByZone.put(zone, shop);
    }

    public String shopFor(String zone) {
        String shop = shopByZone.get(zone);
        if (shop == null) {
            throw new IllegalArgumentException("no shop covers zone '" + zone + "'");
        }
        return shop;
    }

    /** How many zones we can deliver to. */
    public int coverage() {
        return shopByZone.size();
    }

    /** All registered zones, sorted. */
    public List<String> zones() {
        return List.copyOf(shopByZone.keySet());
    }
}
