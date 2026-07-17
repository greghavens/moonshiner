import java.time.LocalDate;
import java.util.Objects;

/** One line item from the point-of-sale: a ticket type sold on a date. */
public final class TicketSale {
    private final LocalDate saleDate;
    private final String ticketType;   // e.g. "ADULT-FULL", "CHILD-HALF"
    private final int quantity;
    private final int unitPriceCents;

    public TicketSale(LocalDate saleDate, String ticketType, int quantity, int unitPriceCents) {
        this.saleDate = Objects.requireNonNull(saleDate, "saleDate");
        this.ticketType = Objects.requireNonNull(ticketType, "ticketType");
        if (quantity < 0) {
            throw new IllegalArgumentException("quantity must not be negative, got " + quantity);
        }
        if (unitPriceCents < 0) {
            throw new IllegalArgumentException("unitPriceCents must not be negative, got " + unitPriceCents);
        }
        this.quantity = quantity;
        this.unitPriceCents = unitPriceCents;
    }

    public LocalDate saleDate() { return saleDate; }
    public String ticketType() { return ticketType; }
    public int quantity() { return quantity; }
    public int unitPriceCents() { return unitPriceCents; }

    /** Line total in cents. */
    public long totalCents() {
        return (long) quantity * unitPriceCents;
    }
}
