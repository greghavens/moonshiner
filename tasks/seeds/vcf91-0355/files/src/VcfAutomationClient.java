import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.Map;

/** A minimal client for the VCF Automation operations in docs/contract.json. */
public final class VcfAutomationClient {
    private final URI baseUri;
    private final String bearerToken;
    private final Duration pollInterval;
    private final HttpClient httpClient;

    public VcfAutomationClient(URI baseUri, String bearerToken, Duration pollInterval) {
        this.baseUri = baseUri;
        this.bearerToken = bearerToken;
        this.pollInterval = pollInterval;
        this.httpClient = HttpClient.newHttpClient();
    }

    /**
     * Submit a deployment action and wait until Get Request observes a terminal status.
     */
    public RequestResult submitDeploymentActionAndWait(
            String deploymentId,
            String actionId,
            Map<String, String> inputs,
            String reason) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("Not implemented");
    }

    public record RequestResult(String requestId, String deploymentId, String status) {}
}
