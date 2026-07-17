package cannery.batch;

/** Assigns the pack tier for a batch. Thresholds agreed with QA in June. */
public class Grader {
    public String grade(Batch b) {
        if (b.weightKg() >= 10.0) {
            return "case";
        }
        if (b.weightKg() >= 5.0) {
            return "flat";
        }
        return "sample";
    }
}
