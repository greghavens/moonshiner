import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.util.Map;
import java.util.Objects;

/** Minimal JDK-only client for the VCF Automation deployment request API. */
public final class VcfAutomationClient {
    private final URI baseUri;
    private final String bearerToken;
    private final HttpClient httpClient;

    public VcfAutomationClient(URI baseUri, String bearerToken, HttpClient httpClient) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.bearerToken = Objects.requireNonNull(bearerToken, "bearerToken");
        this.httpClient = Objects.requireNonNull(httpClient, "httpClient");
    }

    /**
     * Submits a deployment action and polls the resulting asynchronous request
     * until the service reports a terminal state.
     */
    public RequestState submitDeploymentActionAndWait(
            String deploymentId,
            String actionId,
            Map<String, String> inputs,
            String reason) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("not implemented");
    }

    /** The request identity and status returned by VCF Automation. */
    public static final class RequestState {
        private final String id;
        private final String status;

        public RequestState(String id, String status) {
            this.id = Objects.requireNonNull(id, "id");
            this.status = Objects.requireNonNull(status, "status");
        }

        public String id() {
            return id;
        }

        public String status() {
            return status;
        }

        @Override
        public String toString() {
            return "RequestState{id='" + id + "', status='" + status + "'}";
        }
    }
}
