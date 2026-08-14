import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Objects;
import java.util.Optional;

public final class VsanDataProtectionClient {
    private final URI apiBaseUri;
    private final String sessionId;
    private final Duration pollInterval;
    private final HttpClient http;

    public VsanDataProtectionClient(URI apiBaseUri, String sessionId, Duration pollInterval) {
        this.apiBaseUri = Objects.requireNonNull(apiBaseUri, "apiBaseUri");
        this.sessionId = Objects.requireNonNull(sessionId, "sessionId");
        this.pollInterval = Objects.requireNonNull(pollInterval, "pollInterval");
        this.http = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .build();
    }

    public TaskResult createProtectionGroupSnapshotAndWait(
            String cluster,
            String protectionGroup,
            String snapshotName,
            Optional<RetentionPeriod> retention) throws IOException, InterruptedException {
        Objects.requireNonNull(retention, "retention");

        StringBuilder body = new StringBuilder()
                .append("{\"name\":\"")
                .append(escapeJson(snapshotName))
                .append('"');
        retention.ifPresent(value -> body
                .append(",\"retention\":{\"duration\":")
                .append(value.duration())
                .append(",\"unit\":\"")
                .append(escapeJson(value.unit()))
                .append("\"}"));
        body.append('}');

        URI createUri = endpoint(
                "/snapservice/clusters/" + encodePathSegment(cluster)
                        + "/protection-groups/" + encodePathSegment(protectionGroup)
                        + "/snapshots?vmw-task=true");
        HttpRequest request = request(createUri)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body.toString(), StandardCharsets.UTF_8))
                .build();
        HttpResponse<String> response = http.send(
                request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        requireStatus(response, 202, "create snapshot task");

        String taskId = parseJsonString(response.body());
        return new TaskResult(taskId, "SUCCEEDED");
    }

    private HttpRequest.Builder request(URI uri) {
        return HttpRequest.newBuilder(uri)
                .header("Accept", "application/json")
                .header("vmware-api-session-id", sessionId);
    }

    private URI endpoint(String operationPath) {
        String base = apiBaseUri.toString();
        return URI.create((base.endsWith("/") ? base.substring(0, base.length() - 1) : base)
                + operationPath);
    }

    private static void requireStatus(HttpResponse<String> response, int expected, String operation)
            throws IOException {
        if (response.statusCode() != expected) {
            throw new IOException(operation + " returned HTTP " + response.statusCode()
                    + ": " + response.body());
        }
    }

    private static String parseJsonString(String json) throws IOException {
        String value = json.trim();
        if (value.length() < 2 || value.charAt(0) != '"' || value.charAt(value.length() - 1) != '"') {
            throw new IOException("expected a JSON string task identifier");
        }
        StringBuilder decoded = new StringBuilder();
        for (int i = 1; i < value.length() - 1; i++) {
            char current = value.charAt(i);
            if (current != '\\') {
                decoded.append(current);
                continue;
            }
            if (++i >= value.length() - 1) {
                throw new IOException("invalid JSON string escape");
            }
            char escaped = value.charAt(i);
            switch (escaped) {
                case '"', '\\', '/' -> decoded.append(escaped);
                case 'b' -> decoded.append('\b');
                case 'f' -> decoded.append('\f');
                case 'n' -> decoded.append('\n');
                case 'r' -> decoded.append('\r');
                case 't' -> decoded.append('\t');
                default -> throw new IOException("unsupported JSON string escape");
            }
        }
        return decoded.toString();
    }

    private static String escapeJson(String value) {
        Objects.requireNonNull(value, "JSON string");
        StringBuilder escaped = new StringBuilder();
        for (int i = 0; i < value.length(); i++) {
            char current = value.charAt(i);
            switch (current) {
                case '"' -> escaped.append("\\\"");
                case '\\' -> escaped.append("\\\\");
                case '\b' -> escaped.append("\\b");
                case '\f' -> escaped.append("\\f");
                case '\n' -> escaped.append("\\n");
                case '\r' -> escaped.append("\\r");
                case '\t' -> escaped.append("\\t");
                default -> {
                    if (current < 0x20) {
                        escaped.append(String.format("\\u%04x", (int) current));
                    } else {
                        escaped.append(current);
                    }
                }
            }
        }
        return escaped.toString();
    }

    private static String encodePathSegment(String value) {
        Objects.requireNonNull(value, "path segment");
        StringBuilder encoded = new StringBuilder();
        for (byte current : value.getBytes(StandardCharsets.UTF_8)) {
            int octet = current & 0xff;
            if ((octet >= 'a' && octet <= 'z')
                    || (octet >= 'A' && octet <= 'Z')
                    || (octet >= '0' && octet <= '9')
                    || octet == '-' || octet == '.' || octet == '_' || octet == '~') {
                encoded.append((char) octet);
            } else {
                encoded.append('%');
                encoded.append(Character.toUpperCase(Character.forDigit(octet >>> 4, 16)));
                encoded.append(Character.toUpperCase(Character.forDigit(octet & 0xf, 16)));
            }
        }
        return encoded.toString();
    }

    public record RetentionPeriod(long duration, String unit) {}

    public record TaskResult(String taskId, String status) {}
}
