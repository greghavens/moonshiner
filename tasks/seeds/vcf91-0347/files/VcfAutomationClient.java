import java.time.Duration;
import java.util.Map;

/** JDK-only client for the deployment-action subset pinned in docs/contract.json. */
public final class VcfAutomationClient {
    public record OperationResult(String deploymentId, String requestId, String status) {}

    public static final class VcfAutomationException extends RuntimeException {
        public VcfAutomationException(String message) {
            super(message);
        }

        public VcfAutomationException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    private final String baseUrl;
    private final String bearerToken;
    private final Duration pollInterval;

    public VcfAutomationClient(String baseUrl, String bearerToken, Duration pollInterval) {
        if (baseUrl == null || baseUrl.isBlank()) {
            throw new IllegalArgumentException("baseUrl is required");
        }
        if (bearerToken == null || bearerToken.isBlank()) {
            throw new IllegalArgumentException("bearerToken is required");
        }
        if (pollInterval == null || pollInterval.isNegative()) {
            throw new IllegalArgumentException("pollInterval must not be negative");
        }
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.bearerToken = bearerToken;
        this.pollInterval = pollInterval;
    }

    public OperationResult runDeploymentAction(
            String deploymentName,
            String actionId,
            Map<String, Object> inputs,
            String reason) {
        throw new UnsupportedOperationException("TODO: implement the contract-backed async client");
    }
}
