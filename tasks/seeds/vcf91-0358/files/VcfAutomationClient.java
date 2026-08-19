import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.Objects;

/**
 * Focused client for the VCF Automation 9.1 reference operations selected in
 * docs/contract.json. This exercise intentionally uses only the Java standard
 * library.
 */
public final class VcfAutomationClient {
    private final URI baseUri;
    private final String accessToken;
    private final Duration requestTimeout;
    private final HttpClient http;

    public record RequestState(
            String id,
            String deploymentId,
            String name,
            String status,
            String requestedBy,
            int completedTasks,
            int totalTasks,
            String completedAt) {
    }

    public record DeleteResult(RequestState request, int polls) {
    }

    public static final class VcfApiException extends IOException {
        private final int statusCode;

        public VcfApiException(int statusCode, String message) {
            super(message);
            this.statusCode = statusCode;
        }

        public int statusCode() {
            return statusCode;
        }
    }

    public VcfAutomationClient(URI baseUri, String accessToken, Duration requestTimeout) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.accessToken = Objects.requireNonNull(accessToken, "accessToken");
        this.requestTimeout = Objects.requireNonNull(requestTimeout, "requestTimeout");
        this.http = HttpClient.newBuilder()
                .connectTimeout(requestTimeout)
                .followRedirects(HttpClient.Redirect.NEVER)
                .build();
    }

    /**
     * Deletes one deployment and follows the returned request until it reaches
     * a terminal state.
     */
    public DeleteResult deleteDeploymentAndWait(
            String deploymentId,
            Duration pollInterval) throws IOException, InterruptedException {
        // TODO: implement the reference-derived DELETE and request polling flow.
        throw new UnsupportedOperationException("Not implemented");
    }
}
