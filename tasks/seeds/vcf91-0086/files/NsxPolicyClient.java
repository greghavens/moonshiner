import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Base64;
import java.util.List;
import java.util.Objects;

/**
 * Minimal NSX Policy diagnostic client. This project intentionally uses only the
 * JDK so that it can be embedded in a small VCF operations integration.
 */
public final class NsxPolicyClient {
    public record AlarmEvidence(
            String id,
            String severity,
            String message,
            String sourceReference) {
    }

    public record DropEvidence(
            String resourceType,
            String reason,
            String componentName,
            String transportNodeName,
            long sequenceNumber) {
    }

    public record DiagnosticReport(
            String traceflowId,
            List<AlarmEvidence> errorAlarms,
            List<DropEvidence> droppedPackets) {
        public DiagnosticReport {
            Objects.requireNonNull(traceflowId, "traceflowId");
            errorAlarms = List.copyOf(errorAlarms);
            droppedPackets = List.copyOf(droppedPackets);
        }
    }

    private static final String POLICY_BASE = "/policy/api/v1";

    private final URI managerUri;
    private final HttpClient http;
    private final String authorization;

    public NsxPolicyClient(URI managerUri, String username, String password) {
        this.managerUri = Objects.requireNonNull(managerUri, "managerUri");
        Objects.requireNonNull(username, "username");
        Objects.requireNonNull(password, "password");
        this.http = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(5))
                .build();
        this.authorization = "Basic " + Base64.getEncoder().encodeToString(
                (username + ":" + password).getBytes(StandardCharsets.UTF_8));
    }

    /**
     * Diagnose a connectivity failure from NSX evidence.
     *
     * <p>The current implementation is the production regression: it asks only
     * for traceflow data, sends an unset query parameter as an empty value, uses
     * form encoding for a URI path segment, and then guesses that a firewall
     * rule was responsible.</p>
     */
    public DiagnosticReport diagnoseConnectivityFailure(String traceflowId)
            throws IOException, InterruptedException {
        Objects.requireNonNull(traceflowId, "traceflowId");

        String encodedId = URLEncoder.encode(traceflowId, StandardCharsets.UTF_8);
        String path = POLICY_BASE + "/infra/traceflows/" + encodedId
                + "/observations?enforcement_point_path=";
        get(path);

        return new DiagnosticReport(
                traceflowId,
                List.of(new AlarmEvidence(
                        "GUESSED_FIREWALL_FAILURE",
                        "ERROR",
                        "Traffic was probably blocked by a firewall rule",
                        "")),
                List.of());
    }

    private String get(String path) throws IOException, InterruptedException {
        URI target = URI.create(stripTrailingSlash(managerUri.toString()) + path);
        HttpRequest request = HttpRequest.newBuilder(target)
                .timeout(Duration.ofSeconds(5))
                .header("Accept", "application/json")
                .header("Authorization", authorization)
                .GET()
                .build();
        HttpResponse<String> response = http.send(
                request,
                HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new IOException("NSX Policy GET failed with HTTP " + response.statusCode());
        }
        return response.body();
    }

    private static String stripTrailingSlash(String value) {
        int end = value.length();
        while (end > 0 && value.charAt(end - 1) == '/') {
            end--;
        }
        return value.substring(0, end);
    }
}
