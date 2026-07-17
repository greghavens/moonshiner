/**
 * A non-2xx answer from SDDC Manager, decoded from the VCF error envelope.
 * The message deliberately carries only status, error code and the server's
 * message text — never credentials or tokens.
 */
public class VcfApiException extends RuntimeException {

    private final int statusCode;
    private final String errorCode;
    private final String referenceToken;

    public VcfApiException(int statusCode, String errorCode, String message, String referenceToken) {
        super("SDDC Manager returned " + statusCode
                + (errorCode == null ? "" : " (" + errorCode + ")")
                + (message == null ? "" : ": " + message));
        this.statusCode = statusCode;
        this.errorCode = errorCode;
        this.referenceToken = referenceToken;
    }

    public int statusCode() {
        return statusCode;
    }

    public String errorCode() {
        return errorCode;
    }

    public String referenceToken() {
        return referenceToken;
    }
}
