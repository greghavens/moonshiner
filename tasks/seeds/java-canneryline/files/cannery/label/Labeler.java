package cannery.label;

import java.util.Locale;

import cannery.batch.Batch;

/** Renders the jar-label line printed for each batch. */
public class Labeler {
    public String label(Batch b) {
        return b.code() + " | " + b.fruit().toUpperCase(Locale.ROOT) + " | "
                + String.format(Locale.ROOT, "%.1fkg", b.weightKg());
    }
}
