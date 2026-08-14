import java.net.URI;
import java.net.http.HttpClient;
import java.util.List;

/**
 * Small JDK-only client for a three-operation VCF Operations for Networks
 * vCenter change. The implementation belongs entirely in this source file.
 */
public final class OperationsForNetworksClient {

    /** Null means that an optional update field is not set. */
    public record VCenterChange(
            String nickname,
            String notes,
            String username,
            String password) {
    }

    /** The observed outcome of one attempted OpenAPI operation. */
    public record StepResult(
            String operationId,
            int statusCode,
            boolean succeeded,
            String error) {
    }

    /** Ordered outcomes for the change request. */
    public record ChangeReport(String dataSourceId, List<StepResult> steps) {
        public ChangeReport {
            steps = List.copyOf(steps);
        }

        public boolean completed() {
            return steps.size() == 3 && steps.stream().allMatch(StepResult::succeeded);
        }
    }

    private final URI apiBaseUri;
    private final String token;
    private final HttpClient http;

    public OperationsForNetworksClient(URI apiBaseUri, String token, HttpClient http) {
        this.apiBaseUri = apiBaseUri;
        this.token = token;
        this.http = http;
    }

    public ChangeReport applyVCenterChange(String dataSourceId, VCenterChange change) {
        throw new UnsupportedOperationException("implement the spec-derived change workflow");
    }
}
