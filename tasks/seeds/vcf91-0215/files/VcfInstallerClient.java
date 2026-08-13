import java.time.Duration;

/**
 * VCF Installer 9.1 exercise client. Implement this file without dependencies.
 */
public final class VcfInstallerClient {
    public record ProxyConfiguration(
            boolean isEnabled,
            String host,
            Integer port,
            String transferProtocol,
            String username,
            String password,
            Boolean isAuthenticated) {
    }

    public record Task(String id, String name, String status, String creationTimestamp) {
    }

    public static final class VcfApiException extends RuntimeException {
        private final String operationId;
        private final int statusCode;

        public VcfApiException(String operationId, int statusCode) {
            super(operationId + " failed with HTTP status " + statusCode);
            this.operationId = operationId;
            this.statusCode = statusCode;
        }

        public String operationId() {
            return operationId;
        }

        public int statusCode() {
            return statusCode;
        }
    }

    public static final class ProtocolException extends RuntimeException {
        private final String operationId;

        public ProtocolException(String operationId, String problem) {
            super(operationId + " protocol error: " + problem);
            this.operationId = operationId;
        }

        public String operationId() {
            return operationId;
        }
    }

    public static final class TaskFailedException extends RuntimeException {
        private final Task task;

        public TaskFailedException(Task task) {
            super("VCF Installer task ended unsuccessfully");
            this.task = task;
        }

        public Task task() {
            return task;
        }
    }

    public VcfInstallerClient(String baseUrl, String accessToken) {
        throw new UnsupportedOperationException("not implemented");
    }

    public Task updateProxyAndWait(ProxyConfiguration configuration, Duration pollInterval) {
        throw new UnsupportedOperationException("not implemented");
    }
}
