import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.NoSuchElementException;

/**
 * Invoice lifecycle for the trade desk: open, edit, price, settle.
 *
 * Merchandise totals are memoized because the statement print run asks for
 * the same figure dozens of times per invoice. Settling posts every
 * adjustment to the invoice ledger; freight is billed at cost but capped
 * at {@link #FREIGHT_CAP_BPS} of merchandise, and when the cap bites, the
 * difference is posted back as its own FREIGHT_CAP_CREDIT entry so both
 * sides of the charge stay visible to audit. Entries recorded while a
 * settle pass is running are posted in that same pass.
 */
public final class BillingService {

    /** Freight is billed at cost, capped at 5% of merchandise. */
    public static final int FREIGHT_CAP_BPS = 500;

    private final Map<String, Invoice> invoices = new LinkedHashMap<>();
    private final Map<String, Long> totalCache = new HashMap<>();
    private final Map<String, List<String>> ledgers = new LinkedHashMap<>();

    public Invoice open(String id, String customer, int tradeDiscountBps, int promptPayBps) {
        if (invoices.containsKey(id)) {
            throw new IllegalArgumentException("invoice already open: " + id);
        }
        Invoice invoice = new Invoice(id, customer, tradeDiscountBps, promptPayBps);
        invoices.put(id, invoice);
        return invoice;
    }

    public Invoice get(String id) {
        Invoice invoice = invoices.get(id);
        if (invoice == null) {
            throw new NoSuchElementException("unknown invoice: " + id);
        }
        return invoice;
    }

    /** Merchandise due after discount terms; memoized per invoice draft. */
    public long totalDue(String id) {
        Invoice invoice = get(id);
        String key = invoice.id();
        Long cached = totalCache.get(key);
        if (cached != null) {
            return cached;
        }
        long total = Pricing.netMerchandise(invoice);
        totalCache.put(key, total);
        return total;
    }

    /**
     * Post all adjustments to the invoice ledger and return the settled
     * total (merchandise due plus posted adjustments).
     */
    public long settle(String id) {
        Invoice invoice = get(id);
        List<String> ledger = ledgers.computeIfAbsent(id, k -> new ArrayList<>());
        long adjustmentTotal = 0;
        for (Invoice.Adjustment adjustment : invoice.adjustments()) {
            adjustmentTotal += post(invoice, adjustment, ledger);
        }
        return totalDue(id) + adjustmentTotal;
    }

    private long post(Invoice invoice, Invoice.Adjustment adjustment, List<String> ledger) {
        if (adjustment.kind() == Invoice.AdjustmentKind.FREIGHT) {
            long cap = Pricing.bpsShare(Pricing.merchandiseSubtotal(invoice), FREIGHT_CAP_BPS);
            if (adjustment.amountCents() > cap) {
                invoice.addAdjustment("FRT-CAP", Invoice.AdjustmentKind.FREIGHT_CAP_CREDIT,
                        cap - adjustment.amountCents());
            }
        }
        ledger.add(String.format(Locale.ROOT, "%s %+d", adjustment.code(), adjustment.amountCents()));
        return adjustment.amountCents();
    }

    /** Ledger lines posted for an invoice so far, in posting order. */
    public List<String> ledger(String id) {
        get(id);
        return List.copyOf(ledgers.getOrDefault(id, List.of()));
    }

    /** One-line statement summary for the print run. */
    public String statement(String id) {
        Invoice invoice = get(id);
        long due = totalDue(id);
        return String.format(Locale.ROOT, "%s rev %d due %d.%02d USD",
                invoice.id(), invoice.revision(), due / 100, due % 100);
    }
}
