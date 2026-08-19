import java.io.IOException;
import java.net.URI;
import java.time.Duration;
import java.util.List;

/**
 * Rotates the password of a named VCF Automation vSphere cloud account after draining requests
 * that were already in progress.
 */
public final class VcfAutomationCredentialRotator {

    /**
     * Creates a client. Construction must not perform network traffic.
     *
     * @param origin VCF Automation HTTP(S) origin, with no path other than an optional slash
     * @param bearerToken bearer token used for all four contract operations
     * @param apiVersion yyyy-MM-dd API version sent on every request
     * @param pollInterval non-negative delay between observations of an in-progress tracker
     */
    public VcfAutomationCredentialRotator(URI origin, String bearerToken, String apiVersion,
                                           Duration pollInterval) {
        throw new UnsupportedOperationException("not implemented");
    }

    /**
     * Drains current request trackers, looks up {@code accountName}, updates its password, and
     * follows the update tracker to a terminal state.
     */
    public RotationResult rotate(String accountName, String newPassword)
            throws IOException, InterruptedException, RotationFailedException {
        throw new UnsupportedOperationException("not implemented");
    }

    /** Successful terminal result. */
    public record RotationResult(String cloudAccountId, List<String> drainedRequestIds,
                                 String updateRequestId, String status) {
    }

    /** A drain or update tracker reached FAILED. */
    public static final class RotationFailedException extends Exception {
        private final String requestId;

        public RotationFailedException(String requestId) {
            super("VCF Automation request failed: " + requestId);
            this.requestId = requestId;
        }

        public String requestId() {
            return requestId;
        }
    }
}
