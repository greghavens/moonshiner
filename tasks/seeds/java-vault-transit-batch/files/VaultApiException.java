import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/** A non-2xx Vault response, carrying the decoded {"errors": [...]} document. */
public class VaultApiException extends RuntimeException {

    private final int status;
    private final List<String> errors;

    public VaultApiException(int status, List<String> errors) {
        super("vault: HTTP " + status + ": " + String.join("; ", errors));
        this.status = status;
        this.errors = Collections.unmodifiableList(new ArrayList<>(errors));
    }

    public int status() {
        return status;
    }

    public List<String> errors() {
        return errors;
    }

    static VaultApiException fromResponse(int status, String body) {
        List<String> messages = new ArrayList<>();
        try {
            Map<String, Object> doc = Json.parseObject(body);
            Object errs = doc.get("errors");
            if (errs instanceof List<?> list) {
                for (Object e : list) messages.add(String.valueOf(e));
            }
        } catch (RuntimeException ignored) {
            // Non-JSON error body; fall through with what we have.
        }
        if (messages.isEmpty()) messages.add("(no error detail)");
        return new VaultApiException(status, messages);
    }
}
