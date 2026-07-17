package cannery.report;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

import cannery.batch.Batch;
import cannery.batch.Grader;
import cannery.batch.Labeler;

/** End-of-shift yield summary handed to the floor supervisor. */
public class YieldReport {
    private final Grader grader = new Grader();
    private final Labeler labeler = new Labeler();

    public List<String> lines(List<Batch> batches) {
        List<String> out = new ArrayList<>();
        double total = 0.0;
        for (Batch b : batches) {
            out.add(labeler.label(b) + " -> " + grader.grade(b));
            total += b.weightKg();
        }
        out.add(String.format(Locale.ROOT, "TOTAL %.1fkg over %d batches", total, batches.size()));
        return out;
    }
}
