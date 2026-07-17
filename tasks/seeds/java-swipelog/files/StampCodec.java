import java.text.ParseException;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;

/**
 * Canonical wire format for door-controller swipe stamps:
 * "yyyy-MM-dd HH:mm:ss", always UTC, always ROOT locale — the controllers
 * are headless appliances and must not pick up whatever regional settings
 * the ingest host happens to have.
 */
public final class StampCodec {
    private static final SimpleDateFormat WIRE_FORMAT = newWireFormat();

    private static SimpleDateFormat newWireFormat() {
        SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.ROOT);
        f.setTimeZone(TimeZone.getTimeZone("UTC"));
        f.setLenient(false);
        return f;
    }

    private StampCodec() {}

    /** Wire stamp -> epoch millis (UTC). Rejects anything off-format. */
    public static long parseEpochMillis(String stamp) {
        try {
            return WIRE_FORMAT.parse(stamp).getTime();
        } catch (ParseException e) {
            throw new IllegalArgumentException("unparseable swipe stamp: " + stamp, e);
        }
    }

    /** Epoch millis -> wire stamp (UTC). */
    public static String formatEpochMillis(long epochMillis) {
        return WIRE_FORMAT.format(new Date(epochMillis));
    }
}
