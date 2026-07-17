package domain;

/** Domain failure with a stable machine-readable code the desk surfaces verbatim. */
public class PortError extends RuntimeException {
    private final String code;

    public PortError(String code, String message) {
        super(message);
        this.code = code;
    }

    public String code() {
        return code;
    }
}
