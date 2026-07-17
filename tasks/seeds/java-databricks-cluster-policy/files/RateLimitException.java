/** A 429 that survived every retry. Carries the last Retry-After value. */
public class RateLimitException extends DbxApiException {

    private final long retryAfterSeconds;

    public RateLimitException(String errorCode, String apiMessage, long retryAfterSeconds) {
        super(429, errorCode, apiMessage);
        this.retryAfterSeconds = retryAfterSeconds;
    }

    public long retryAfterSeconds() {
        return retryAfterSeconds;
    }
}
