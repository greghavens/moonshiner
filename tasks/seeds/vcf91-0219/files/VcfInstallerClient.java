import java.util.List;

/** Single-file, dependency-free client for the focused VCF Installer contract. */
public final class VcfInstallerClient {
    public record ProxyConfiguration(
            Boolean isEnabled,
            String host,
            Integer port,
            String transferProtocol,
            String username,
            String password,
            Boolean isAuthenticated) {
    }

    /** Writable DepotAccount members, in specification declaration order. */
    public record DepotAccount(
            String username,
            String password,
            String downloadToken,
            String downloadActivationCode) {
    }

    /** isOfflineDepot is required whenever this nested object is present. */
    public record DepotConfiguration(
            boolean isOfflineDepot,
            String hostname,
            Integer port,
            String url) {
    }

    public record DepotSettings(
            DepotAccount vmwareAccount,
            DepotAccount offlineAccount,
            DepotConfiguration depotConfiguration) {
    }

    public enum Outcome {
        ACCEPTED,
        FAILED,
        PARTIAL_FAILURE
    }

    public enum StepStatus {
        NOT_RUN,
        ACCEPTED,
        FAILED
    }

    public record StepResult(
            String operationId,
            StepStatus status,
            int httpStatus,
            String taskId,
            String errorCode,
            String errorMessage) {
    }

    public record ChangeReport(Outcome outcome, List<StepResult> steps) {
        public ChangeReport {
            steps = List.copyOf(steps);
        }
    }

    public abstract static class ChangeException extends Exception {
        private final String operationId;
        private final ChangeReport report;

        protected ChangeException(String message, String operationId, ChangeReport report) {
            super(message);
            this.operationId = operationId;
            this.report = report;
        }

        protected ChangeException(
                String message, String operationId, ChangeReport report, Throwable cause) {
            super(message, cause);
            this.operationId = operationId;
            this.report = report;
        }

        public final String operationId() {
            return operationId;
        }

        public final ChangeReport report() {
            return report;
        }
    }

    public static final class VcfApiException extends ChangeException {
        private final int statusCode;
        private final String errorCode;
        private final String errorType;
        private final String apiMessage;
        private final String remediationMessage;
        private final String referenceToken;

        public VcfApiException(
                String operationId,
                int statusCode,
                String errorCode,
                String errorType,
                String apiMessage,
                String remediationMessage,
                String referenceToken,
                ChangeReport report) {
            super(operationId + " failed with HTTP status " + statusCode, operationId, report);
            this.statusCode = statusCode;
            this.errorCode = errorCode;
            this.errorType = errorType;
            this.apiMessage = apiMessage;
            this.remediationMessage = remediationMessage;
            this.referenceToken = referenceToken;
        }

        public int statusCode() {
            return statusCode;
        }

        public String errorCode() {
            return errorCode;
        }

        public String errorType() {
            return errorType;
        }

        public String apiMessage() {
            return apiMessage;
        }

        public String remediationMessage() {
            return remediationMessage;
        }

        public String referenceToken() {
            return referenceToken;
        }
    }

    public static final class ProtocolException extends ChangeException {
        public ProtocolException(String operationId, ChangeReport report) {
            super(operationId + " returned an invalid response", operationId, report);
        }

        public ProtocolException(String operationId, ChangeReport report, Throwable cause) {
            super(operationId + " returned an invalid response", operationId, report, cause);
        }
    }

    public static final class TransportException extends ChangeException {
        public TransportException(String operationId, ChangeReport report, Throwable cause) {
            super(operationId + " transport failed", operationId, report, cause);
        }
    }

    public VcfInstallerClient(String baseUrl, String accessToken) {
        // TODO: implement validation and client construction.
    }

    /** Applies proxy, depot settings, and metadata sync in contract order. */
    public ChangeReport configureDepotAccess(ProxyConfiguration proxy, DepotSettings depot)
            throws ChangeException {
        throw new UnsupportedOperationException("not implemented");
    }
}
