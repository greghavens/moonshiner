package cannery.batch;

/** One cooked batch coming off the line, as entered at the intake scale. */
public class Batch {
    private final String code;
    private final String fruit;
    private final double weightKg;

    public Batch(String code, String fruit, double weightKg) {
        if (weightKg < 0) {
            throw new IllegalArgumentException("weight cannot be negative");
        }
        this.code = code;
        this.fruit = fruit;
        this.weightKg = weightKg;
    }

    public String code() {
        return code;
    }

    public String fruit() {
        return fruit;
    }

    public double weightKg() {
        return weightKg;
    }
}
