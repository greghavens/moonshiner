import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Sorts scanned job tickets out of the front-desk drop folder into the
 * right department queue on the print server.
 *
 * The desk scanner names files like INV-20260712-004.PDF: the prefix
 * before the first dash picks the queue. Anything we don't recognize
 * goes to the holding queue for a human to look at.
 */
public class DropRouter {

    // Queue shares on the print server, keyed by ticket prefix.
    // The desk scanner stages everything under C:\users\frontdesk\drop first,
    // then the mover copies each ticket to the share we pick here.
    private static final Map<String, String> QUEUES = new LinkedHashMap<>();
    static {
        QUEUES.put("PROOF", "\\\\printsrv\\queues\\prepress");
        QUEUES.put("JOB", "\\\\printsrv\\queues\\production");
        QUEUES.put("INV", "\\\\printsrv\\queues\\accounts");
    }

    // Tickets nobody claims are swept to C:\Archive\drop on Fridays.
    static final String HOLDING = "\\\\printsrv\\queues\\holding";

    /** Queue share for a scanned file name; unknown prefixes go to holding. */
    public static String route(String fileName) {
        String prefix = prefixOf(fileName);
        String queue = QUEUES.get(prefix);
        return queue != null ? queue : HOLDING;
    }

    /** Prefix before the first dash, uppercased so hand-renamed files still match. */
    static String prefixOf(String fileName) {
        String base = fileName.trim();
        int dash = base.indexOf('-');
        if (dash <= 0) {
            return "";
        }
        return base.substring(0, dash).toUpperCase(Locale.ROOT);
    }

    /**
     * Scanner output is shouty; the mover wants tidy names. Keep the base
     * name as scanned but lowercase the extension and squeeze spaces to
     * single underscores.
     */
    public static String normalize(String fileName) {
        String name = fileName.trim().replaceAll("\\s+", "_");
        int dot = name.lastIndexOf('.');
        if (dot <= 0 || dot == name.length() - 1) {
            return name;
        }
        return name.substring(0, dot) + name.substring(dot).toLowerCase(Locale.ROOT);
    }

    /** One line of the mover's manifest: normalized name, arrow, target share. */
    public static String manifestLine(String fileName) {
        return normalize(fileName) + " -> " + route(fileName);
    }
}
