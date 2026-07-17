package cannery.batch;

/** Assigns the pack tier for a batch. */
public class Grader {
    public String grade(Batch b) {
        if (b.weightKg() >= 12.0) {
            return "case";
        }
        if (b.weightKg() >= 4.0) {
            return "flat";
        }
        return "sample";
    }
}
