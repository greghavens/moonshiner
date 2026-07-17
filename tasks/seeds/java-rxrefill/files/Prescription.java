import java.time.LocalDate;
import java.util.Objects;

/** One prescription on file: what was dispensed, when, and for how long. */
public final class Prescription {
    private final String rxNumber;
    private final String drug;
    private final LocalDate lastFill;
    private final int daysSupply;

    public Prescription(String rxNumber, String drug, LocalDate lastFill, int daysSupply) {
        this.rxNumber = Objects.requireNonNull(rxNumber, "rxNumber");
        this.drug = Objects.requireNonNull(drug, "drug");
        this.lastFill = Objects.requireNonNull(lastFill, "lastFill");
        if (daysSupply <= 0) {
            throw new IllegalArgumentException("daysSupply must be positive, got " + daysSupply);
        }
        this.daysSupply = daysSupply;
    }

    public String rxNumber() { return rxNumber; }
    public String drug() { return drug; }
    public LocalDate lastFill() { return lastFill; }
    public int daysSupply() { return daysSupply; }

    @Override
    public String toString() {
        return rxNumber + " " + drug + " (" + daysSupply + "-day, last fill " + lastFill + ")";
    }
}
