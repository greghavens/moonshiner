import java.util.Locale;

/** Shared helpers for hookup-class codes coming in from the booking feed. */
public final class Hookups {
    private Hookups() {
    }

    /** The booking feed sends mixed-case codes; we store them normalized. */
    public static String normalize(String code) {
        return code.trim().toLowerCase(Locale.ROOT);
    }
}
