import java.net.http.HttpClient;

/**
 * Dependency-free client for the focused VCF 9.1 vCenter attestation
 * diagnostics workflow.
 */
public final class VcenterAttestationDiagnosticsClient {
    public record Diagnosis(
            String taskStatus,
            String taskErrorType,
            String taskMessage,
            String eventLogType,
            String eventEvidence,
            boolean eventLogTruncated,
            String rootCause,
            String supportBundleTaskId) {
    }

    public static final class VcenterApiException extends RuntimeException {
        private final int statusCode;
        private final String responseBody;

        public VcenterApiException(int statusCode, String responseBody) {
            super("vCenter API returned HTTP " + statusCode);
            this.statusCode = statusCode;
            this.responseBody = responseBody;
        }

        public int statusCode() {
            return statusCode;
        }

        public String responseBody() {
            return responseBody;
        }
    }

    public VcenterAttestationDiagnosticsClient(
            String vcenterBaseUrl,
            String sessionId,
            HttpClient httpClient) {
        throw new UnsupportedOperationException("TODO");
    }

    public Diagnosis diagnoseFailedAttestation(
            String taskId,
            String hostId,
            String tpmId,
            String supportDescription) {
        throw new UnsupportedOperationException("TODO");
    }
}
