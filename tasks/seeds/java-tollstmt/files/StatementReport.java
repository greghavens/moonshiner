import java.util.List;
import java.util.Locale;
import java.util.TreeSet;

/**
 * Renders the month-end statement file for a plaza feed.
 *
 * Layout (one statement block per account, accounts in sorted order;
 * within a block the crossings appear in feed order):
 *
 *   ACCOUNT TP-00104
 *     PLZ-030 day 2 $0.95
 *   SUBTOTAL TP-00104 trips 1 $0.95
 *   ...
 *   TOTAL $12.95
 *
 * The archive stores a checksum of every statement file, so the output
 * bytes are contract: same feed in, same bytes out, forever.
 */
public final class StatementReport {

    private StatementReport() {}

    public static String money(long cents) {
        return String.format(Locale.ROOT, "$%d.%02d", cents / 100, cents % 100);
    }

    public static String generate(List<Crossing> crossings) {
        TreeSet<String> accounts = new TreeSet<>();
        for (Crossing c : crossings) {
            accounts.add(c.account());
        }
        String out = "";
        long grandTotal = 0;
        for (String account : accounts) {
            out += "ACCOUNT " + account + "\n";
            long subtotal = 0;
            int trips = 0;
            for (Crossing c : crossings) {
                if (c.account().equals(account)) {
                    out += "  " + c.plaza() + " day " + c.day() + " " + money(c.cents()) + "\n";
                    subtotal += c.cents();
                    trips++;
                }
            }
            out += "SUBTOTAL " + account + " trips " + trips + " " + money(subtotal) + "\n";
            grandTotal += subtotal;
        }
        out += "TOTAL " + money(grandTotal) + "\n";
        return out;
    }
}
