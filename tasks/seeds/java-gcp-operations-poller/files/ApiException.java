import java.util.List;
import java.util.Map;

/**
 * Decoded Google API error envelope: {"error": {"code", "message", "status",
 * "details": [...]}} where code is the HTTP status, status is the
 * google.rpc.Code enum name, and details carries typed @type payloads.
 */
final class ApiException extends RuntimeException {
    private final int httpCode;
    private final String status;
    private final List<Object> details;

    ApiException(int httpCode, String status, String message, List<Object> details) {
        super(message);
        this.httpCode = httpCode;
        this.status = status;
        this.details = details;
    }

    int httpCode() {
        return httpCode;
    }

    String status() {
        return status;
    }

    List<Object> details() {
        return details;
    }

    static ApiException decode(int httpCode, String body) {
        try {
            Map<String, Object> root = Json.object(Json.parse(body));
            Map<String, Object> error = Json.object(root.get("error"));
            String status = error.get("status") == null ? "" : error.get("status").toString();
            String message = error.get("message") == null ? "" : error.get("message").toString();
            List<Object> details = error.get("details") == null ? List.of() : Json.array(error.get("details"));
            return new ApiException(httpCode, status, message, details);
        } catch (RuntimeException e) {
            return new ApiException(httpCode, "", "unparseable error body (HTTP " + httpCode + ")", List.of());
        }
    }
}
