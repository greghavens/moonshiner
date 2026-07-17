/** A non-success Microsoft Graph response, decoded from the error envelope. */
public class GraphApiException extends RuntimeException {
    private final int statusCode;
    private final String errorCode;

    public GraphApiException(int statusCode, String errorCode, String message) {
        super(message);
        this.statusCode = statusCode;
        this.errorCode = errorCode;
    }

    public int statusCode() {
        return statusCode;
    }

    public String errorCode() {
        return errorCode;
    }
}
