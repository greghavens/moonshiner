import java.util.List;

/**
 * End-of-day export for the accounting system. Fixed file contract:
 *
 *   header:  date,ticket_type,qty,unit_price,total
 *   body:    one row per sale, prices in dollars with exactly two decimals
 *   footer:  a TOTAL row carrying the unit count and the grand total
 *
 * '\n' line separators, no trailing newline. The importer on the
 * accounting side is strict: exactly five fields per row, point-decimal
 * amounts, identical bytes no matter which machine ran the batch.
 */
public final class SalesCsvExporter {
    public static final String HEADER = "date,ticket_type,qty,unit_price,total";

    public String export(List<TicketSale> sales) {
        StringBuilder csv = new StringBuilder(HEADER);
        long grandTotalCents = 0;
        int units = 0;
        for (TicketSale s : sales) {
            grandTotalCents += s.totalCents();
            units += s.quantity();
            csv.append('\n').append(row(s));
        }
        csv.append('\n').append(String.format("TOTAL,,%d,,%.2f", units, grandTotalCents / 100.0));
        return csv.toString();
    }

    private String row(TicketSale s) {
        return String.format("%s,%s,%d,%.2f,%.2f",
                s.saleDate(), s.ticketType(), s.quantity(),
                s.unitPriceCents() / 100.0, s.totalCents() / 100.0);
    }
}
