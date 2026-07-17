import java.util.Map;

/**
 * A Kubernetes API failure decoded from a meta/v1 Status body.
 * Carries the Status code/reason/message; never any credentials.
 */
public final class KubeStatusException extends RuntimeException {

    public final int code;
    public final String reason;
    public final String statusMessage;

    public KubeStatusException(int code, String reason, String statusMessage) {
        super(code + " " + reason + ": " + statusMessage);
        this.code = code;
        this.reason = reason;
        this.statusMessage = statusMessage;
    }

    /** Decode a non-2xx response body (a meta/v1 Status JSON) into an exception. */
    public static KubeStatusException fromStatusBody(int httpStatus, String body) {
        try {
            Object parsed = Json.parse(body);
            if (parsed instanceof Map) {
                Map<String, Object> st = Json.asObject(parsed);
                int code = st.get("code") instanceof Double d ? (int) (double) d : httpStatus;
                String reason = st.get("reason") instanceof String s ? s : "Unknown";
                String message = st.get("message") instanceof String s ? s : "(no Status body)";
                return new KubeStatusException(code, reason, message);
            }
        } catch (RuntimeException ignored) {
            // fall through: not a Status body
        }
        return new KubeStatusException(httpStatus, "Unknown", "(no Status body)");
    }
}
