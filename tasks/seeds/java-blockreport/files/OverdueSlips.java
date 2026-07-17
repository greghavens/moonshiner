import java.util.List;
import java.util.Locale;

/**
 * Nightly overdue artifacts for the Riverbend branch system: the
 * warehouse-import SQL and the 32-column returns-desk printer slips.
 * Templates were moved onto text blocks when the batch jobs left Java 8.
 */
public class OverdueSlips {

    /** One loan that is past due as of the report date. */
    public record Loan(String title, int daysLate, double fine) {}

    static final String EXPORT_SQL = """
            INSERT INTO overdue_export (branch, card_no, title, days_late)\n
            SELECT b.code, l.card_no, i.title, (:as_of - l.due_on)\n
            FROM loans l\n
            JOIN items i ON i.id = l.item_id\n
            JOIN branches b ON b.id = l.branch_id\n
            WHERE l.returned_on IS NULL AND l.due_on < :as_of\n
            ORDER BY b.code, l.card_no;
            """;

    static final String SLIP_BANNER = """
            RIVERBEND BRANCH LIBRARY        
            OVERDUE ITEMS - KEEP SLIP       
            --------------------------------
            """;

    /** Header comment plus the importer statement, ready to write as .sql. */
    public static String exportSql(String asOfIso) {
        return "-- overdue export, generated for as_of=" + asOfIso + "\n" + EXPORT_SQL;
    }

    /**
     * One tear-off slip for the returns desk. The desk printer is fixed
     * pitch and the perforation check needs every record exactly 32
     * characters wide.
     */
    public static String slip(String cardNo, List<Loan> loans) {
        StringBuilder sb = new StringBuilder();
        sb.append(SLIP_BANNER);
        sb.append(String.format(Locale.ROOT, "%-32s\n", "CARD " + cardNo));
        double total = 0.0;
        for (Loan loan : loans) {
            String title = loan.title();
            if (title.length() > 22) {
                title = title.substring(0, 22);
            }
            String fine = String.format(Locale.ROOT, "$%.2f", loan.fine());
            sb.append(String.format(Locale.ROOT, "%-22s%4d%6s\n", title, loan.daysLate(), fine));
            total += loan.fine();
        }
        sb.append("--------------------------------\n");
        String totalStr = String.format(Locale.ROOT, "$%.2f", total);
        sb.append(String.format(Locale.ROOT, "%-26s%6s\n", "TOTAL DUE", totalStr));
        return sb.toString();
    }
}
