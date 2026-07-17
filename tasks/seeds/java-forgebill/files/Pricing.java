/**
 * Money math for trade invoices. All amounts are integer minor units
 * (cents); percentage terms are expressed in basis points (1 bp = 0.01%)
 * and shares round half up, per the trade-agreement boilerplate.
 */
public final class Pricing {

    private Pricing() {}

    /** Share of {@code amountCents} at {@code bps} basis points, rounded half up. */
    public static long bpsShare(long amountCents, int bps) {
        return (amountCents * bps + 5_000L) / 10_000L;
    }

    public static long lineTotal(Invoice.Line line) {
        return line.quantity() * line.unitCents();
    }

    public static long merchandiseSubtotal(Invoice invoice) {
        long sum = 0;
        for (Invoice.Line line : invoice.lines()) {
            sum += lineTotal(line);
        }
        return sum;
    }

    /** Negotiated trade discount, taken on the merchandise subtotal. */
    public static long tradeDiscount(Invoice invoice) {
        return bpsShare(merchandiseSubtotal(invoice), invoice.tradeDiscountBps());
    }

    /**
     * Early-settlement discount ("2/10 net 30" style terms), taken on the
     * subtotal remaining after the trade discount.
     */
    public static long promptPayDiscount(Invoice invoice) {
        long base = merchandiseSubtotal(invoice) - tradeDiscount(invoice);
        return (long) (base * (invoice.promptPayBps() / 10_000.0));
    }

    /** Merchandise due after both discount terms. */
    public static long netMerchandise(Invoice invoice) {
        return merchandiseSubtotal(invoice) - tradeDiscount(invoice) - promptPayDiscount(invoice);
    }
}
