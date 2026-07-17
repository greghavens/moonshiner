import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.NoSuchElementException;

/**
 * A trade invoice for a fabrication-supply account: merchandise line items
 * in integer cents plus post-merchandise adjustments (freight, handling
 * fees, credits). Every mutation bumps {@link #revision()} so downstream
 * consumers can tell one draft of an invoice from the next.
 */
public final class Invoice {

    /** One merchandise line. Prices are integer cents, never floats. */
    public static final class Line {
        private final String sku;
        private final String description;
        private int quantity;
        private final long unitCents;

        Line(String sku, String description, int quantity, long unitCents) {
            this.sku = sku;
            this.description = description;
            this.quantity = quantity;
            this.unitCents = unitCents;
        }

        public String sku() { return sku; }
        public String description() { return description; }
        public int quantity() { return quantity; }
        public long unitCents() { return unitCents; }
    }

    public enum AdjustmentKind { FREIGHT, FREIGHT_CAP_CREDIT, PALLET_FEE }

    /** A post-merchandise adjustment; positive = charge, negative = credit. */
    public static final class Adjustment {
        private final String code;
        private final AdjustmentKind kind;
        private final long amountCents;

        Adjustment(String code, AdjustmentKind kind, long amountCents) {
            this.code = code;
            this.kind = kind;
            this.amountCents = amountCents;
        }

        public String code() { return code; }
        public AdjustmentKind kind() { return kind; }
        public long amountCents() { return amountCents; }
    }

    private final String id;
    private final String customer;
    private final int tradeDiscountBps;
    private final int promptPayBps;
    private final List<Line> lines = new ArrayList<>();
    private final List<Adjustment> adjustments = new ArrayList<>();
    private int revision;

    Invoice(String id, String customer, int tradeDiscountBps, int promptPayBps) {
        this.id = id;
        this.customer = customer;
        this.tradeDiscountBps = tradeDiscountBps;
        this.promptPayBps = promptPayBps;
    }

    public String id() { return id; }
    public String customer() { return customer; }
    public int tradeDiscountBps() { return tradeDiscountBps; }
    public int promptPayBps() { return promptPayBps; }

    /** Bumped on every line or adjustment mutation. */
    public int revision() { return revision; }

    public List<Line> lines() { return Collections.unmodifiableList(lines); }
    public List<Adjustment> adjustments() { return Collections.unmodifiableList(adjustments); }

    public void addLine(String sku, String description, int quantity, long unitCents) {
        if (quantity <= 0) {
            throw new IllegalArgumentException("quantity must be positive: " + quantity);
        }
        if (unitCents < 0) {
            throw new IllegalArgumentException("unit price must not be negative: " + unitCents);
        }
        lines.add(new Line(sku, description, quantity, unitCents));
        revision++;
    }

    public void setQuantity(String sku, int quantity) {
        if (quantity <= 0) {
            throw new IllegalArgumentException("quantity must be positive: " + quantity);
        }
        for (Line line : lines) {
            if (line.sku.equals(sku)) {
                line.quantity = quantity;
                revision++;
                return;
            }
        }
        throw new NoSuchElementException("no line for sku " + sku);
    }

    public Adjustment addAdjustment(String code, AdjustmentKind kind, long amountCents) {
        Adjustment adjustment = new Adjustment(code, kind, amountCents);
        adjustments.add(adjustment);
        revision++;
        return adjustment;
    }
}
