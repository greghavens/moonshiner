/**
 * A Databricks REST failure decoded from the standard {error_code, message}
 * envelope. The exception text never contains credentials.
 */
public class DbxApiException extends RuntimeException {

    private final int statusCode;
    private final String errorCode;
    private final String apiMessage;

    public DbxApiException(int statusCode, String errorCode, String apiMessage) {
        super(errorCode + " (HTTP " + statusCode + "): " + apiMessage);
        this.statusCode = statusCode;
        this.errorCode = errorCode;
        this.apiMessage = apiMessage;
    }

    public int statusCode() {
        return statusCode;
    }

    public String errorCode() {
        return errorCode;
    }

    public String apiMessage() {
        return apiMessage;
    }
}
