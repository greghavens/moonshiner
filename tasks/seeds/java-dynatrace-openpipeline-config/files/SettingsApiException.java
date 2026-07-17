import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * A Settings API error envelope ({"error": {code, message,
 * constraintViolations}}), or a SettingsObjectResponse carrying the same
 * error shape for a single write.
 */
public class SettingsApiException extends RuntimeException {

    private final int status;
    private final int errorCode;
    private final List<Map<String, Object>> violations;

    public SettingsApiException(int status, int errorCode, String message,
                                List<Map<String, Object>> violations) {
        super("Dynatrace settings API error " + status + ": " + message);
        this.status = status;
        this.errorCode = errorCode;
        this.violations = violations;
    }

    public int status() {
        return status;
    }

    public int errorCode() {
        return errorCode;
    }

    public List<Map<String, Object>> violations() {
        return violations;
    }

    @SuppressWarnings("unchecked")
    public static SettingsApiException fromBody(int status, String body) {
        int code = status;
        String message = "";
        List<Map<String, Object>> violations = new ArrayList<>();
        try {
            Map<String, Object> doc = Json.parseObject(body);
            Object error = doc.get("error");
            if (error instanceof Map<?, ?> err) {
                Object c = err.get("code");
                if (c instanceof Double d) {
                    code = (int) (double) d;
                }
                Object m = err.get("message");
                if (m instanceof String s) {
                    message = s;
                }
                Object cv = err.get("constraintViolations");
                if (cv instanceof List<?> list) {
                    for (Object v : list) {
                        violations.add((Map<String, Object>) v);
                    }
                }
            }
        } catch (RuntimeException ignored) {
            // non-JSON error body: keep the status-only exception
        }
        return new SettingsApiException(status, code, message, violations);
    }
}
