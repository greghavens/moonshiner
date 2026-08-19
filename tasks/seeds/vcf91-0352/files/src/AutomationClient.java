import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/** Minimal VCF Automation blueprint client. */
public final class AutomationClient {
    private final URI baseUri;
    private final String bearerToken;
    private final HttpClient httpClient;

    public AutomationClient(URI baseUri, String bearerToken) {
        this(baseUri, bearerToken, HttpClient.newHttpClient());
    }

    AutomationClient(URI baseUri, String bearerToken, HttpClient httpClient) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.bearerToken = Objects.requireNonNull(bearerToken, "bearerToken");
        this.httpClient = Objects.requireNonNull(httpClient, "httpClient");
    }

    public SubmissionOutcome submitBlueprintIfValid(
            String blueprintName,
            String projectId,
            String deploymentName,
            Map<String, Object> inputs) throws IOException, InterruptedException {
        Objects.requireNonNull(blueprintName, "blueprintName");
        Objects.requireNonNull(projectId, "projectId");
        Objects.requireNonNull(deploymentName, "deploymentName");
        Objects.requireNonNull(inputs, "inputs");

        // Implement the lookup -> validation -> conditional create workflow.
        throw new UnsupportedOperationException("TODO");
    }

    public record SubmissionOutcome(
            boolean submitted,
            String requestId,
            List<String> validationMessages) {
        public SubmissionOutcome {
            validationMessages = List.copyOf(validationMessages);
        }
    }
}
