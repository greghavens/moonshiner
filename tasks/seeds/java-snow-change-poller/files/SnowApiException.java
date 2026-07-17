/**
 * A non-2xx answer from the instance. Carries the fields of ServiceNow's
 * REST error envelope {"error": {"message", "detail"}, "status": "failure"}
 * plus the Retry-After header value (seconds) when the instance sent one.
 * Exception text stays free of credentials.
 */
public class SnowApiException extends Exception {
    public final int statusCode;
    public final String error;
    public final String detail;
    public final int retryAfter; // seconds from Retry-After; 0 when absent

    public SnowApiException(int statusCode, String error, String detail, int retryAfter) {
        super("ServiceNow API error " + statusCode + ": " + error);
        this.statusCode = statusCode;
        this.error = error;
        this.detail = detail == null ? "" : detail;
        this.retryAfter = retryAfter;
    }
}
