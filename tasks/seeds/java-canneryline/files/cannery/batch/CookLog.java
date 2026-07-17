package cannery;

import java.util.ArrayList;
import java.util.List;

/** Running log of cook cycles, one entry per kettle run. */
public class CookLog {
    private final List<String> entries = new ArrayList<>();

    public void record(String batchCode, int minutes) {
        if (minutes <= 0) {
            throw new IllegalArgumentException("cook minutes must be positive");
        }
        entries.add(batchCode + ":" + minutes + "m");
    }

    public List<String> entries() {
        return List.copyOf(entries);
    }

    public int totalMinutes() {
        int total = 0;
        for (String entry : entries) {
            int colon = entry.indexOf(':');
            total += Integer.parseInt(entry.substring(colon + 1, entry.length() - 1));
        }
        return total;
    }
}
