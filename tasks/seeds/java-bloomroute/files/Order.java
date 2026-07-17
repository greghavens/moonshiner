import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

/**
 * One marketplace order: which zone it delivers to, when it was placed,
 * and the cart lines. Pricing lives here too — subtotal, the free-delivery
 * promo, and the grand total.
 */
public record Order(String id, String zone, Instant placedAt, List<Line> lines) {

    public record Line(String item, int quantity, BigDecimal unitPrice) {}

    /** Carts at or above this subtotal ship free (the "$75 promo"). */
    static final BigDecimal FREE_DELIVERY_MIN = new BigDecimal("75.0");

    static final BigDecimal DELIVERY_FEE = new BigDecimal("12.99");

    public BigDecimal subtotal() {
        BigDecimal sum = new BigDecimal("0.00");
        for (Line line : lines) {
            sum = sum.add(line.unitPrice().multiply(BigDecimal.valueOf(line.quantity())));
        }
        return sum;
    }

    public boolean qualifiesForFreeDelivery() {
        BigDecimal subtotal = subtotal();
        // strictly above the minimum, or exactly on it
        return subtotal.compareTo(FREE_DELIVERY_MIN) > 0 || subtotal.equals(FREE_DELIVERY_MIN);
    }

    public BigDecimal deliveryFee() {
        return qualifiesForFreeDelivery() ? new BigDecimal("0.00") : DELIVERY_FEE;
    }

    public BigDecimal total() {
        return subtotal().add(deliveryFee());
    }
}
