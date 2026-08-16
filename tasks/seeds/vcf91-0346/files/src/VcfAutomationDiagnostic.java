import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Instant;
import java.util.List;
import java.util.Objects;

/**
 * Collects VCF Automation request, event, and log evidence for a failed request.
 *
 * <p>The REST subset implemented by this class is documented in
 * {@code docs/contract.json}.</p>
 */
public final class VcfAutomationDiagnostic {
    private final URI baseUri;
    private final String bearerToken;
    private final HttpClient httpClient;

    public VcfAutomationDiagnostic(URI baseUri, String bearerToken) {
        this(baseUri, bearerToken, HttpClient.newHttpClient());
    }

    VcfAutomationDiagnostic(URI baseUri, String bearerToken, HttpClient httpClient) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.bearerToken = Objects.requireNonNull(bearerToken, "bearerToken");
        this.httpClient = Objects.requireNonNull(httpClient, "httpClient");
    }

    public Diagnosis diagnose(String requestId) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("diagnose is not implemented");
    }

    public record Diagnosis(RequestSummary request, List<EventEvidence> events) {
        public Diagnosis {
            Objects.requireNonNull(request, "request");
            events = List.copyOf(events);
        }
    }

    public record RequestSummary(String id, String name, String status, String details) {
    }

    public record EventEvidence(
            String id,
            String name,
            String resourceName,
            String resourceType,
            String details,
            Instant timestamp,
            boolean userEvent,
            boolean hasLogs,
            List<LogEntry> logs,
            String downloadedLogContent) {
        public EventEvidence {
            logs = List.copyOf(logs);
        }
    }

    public record LogEntry(String id, long rownum, Instant timestamp, String message, boolean eof) {
    }
}
