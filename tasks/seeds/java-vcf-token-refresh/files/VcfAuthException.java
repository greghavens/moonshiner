/**
 * Authentication with SDDC Manager failed and could not be recovered by a
 * token refresh. Like every exception in this client it must never carry
 * token material or passwords in its message.
 */
public class VcfAuthException extends VcfApiException {

    public VcfAuthException(int statusCode, String errorCode, String message, String referenceToken) {
        super(statusCode, errorCode, message, referenceToken);
    }
}
