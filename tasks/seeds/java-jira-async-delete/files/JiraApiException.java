import java.util.List;
import java.util.Map;

/** A non-2xx answer from the Jira Cloud REST API, with its decoded error collection. */
public class JiraApiException extends RuntimeException {
    private final int status;
    private final List<String> errorMessages;

    public JiraApiException(int status, List<String> errorMessages, String message) {
        super(message);
        this.status = status;
        this.errorMessages = List.copyOf(errorMessages);
    }

    public int status() {
        return status;
    }

    public List<String> errorMessages() {
        return errorMessages;
    }

    /** Decodes Jira's ErrorCollection body ({"errorMessages": [...], "errors": {...}}). */
    static JiraApiException of(int status, String body) {
        List<String> messages = List.of();
        try {
            Map<String, Object> parsed = Json.object(Json.parse(body));
            Object raw = parsed.get("errorMessages");
            if (raw instanceof List<?> list) {
                messages = list.stream().map(String::valueOf).toList();
            }
        } catch (RuntimeException ignored) {
            // Not a JSON error collection; keep the status-only message.
        }
        String detail = messages.isEmpty() ? "(no error messages)" : String.join("; ", messages);
        return new JiraApiException(status, messages, "Jira returned HTTP " + status + ": " + detail);
    }
}
