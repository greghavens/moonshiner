import java.io.IOException;
import java.net.URI;
import java.time.Duration;
import java.util.List;

/**
 * Complete this dependency-free VCF 9.1 vCenter Automation client.
 */
public final class VcenterEvcClient {
    public record FeatureMask(String key, String name, String value) {
    }

    public record EvcMode(String key, List<FeatureMask> masks) {
    }

    public record ApplyResult(
            String precheckTaskId,
            String mutationTaskId,
            boolean clearing) {
    }

    public static final class VcenterApiException extends IOException {
        private static final long serialVersionUID = 1L;

        private final String operationId;
        private final int statusCode;
        private final byte[] responseBody;

        public VcenterApiException(
                String operationId,
                int statusCode,
                byte[] responseBody) {
            super("vCenter operation " + operationId
                    + " returned HTTP " + statusCode);
            this.operationId = operationId;
            this.statusCode = statusCode;
            this.responseBody = responseBody.clone();
        }

        public String operationId() {
            return operationId;
        }

        public int statusCode() {
            return statusCode;
        }

        public byte[] responseBody() {
            return responseBody.clone();
        }
    }

    public static final class PrecheckFailedException extends IOException {
        private static final long serialVersionUID = 1L;

        private final String taskId;
        private final String status;
        private final int checkResultCount;

        public PrecheckFailedException(
                String message,
                String taskId,
                String status,
                int checkResultCount) {
            super(message);
            this.taskId = taskId;
            this.status = status;
            this.checkResultCount = checkResultCount;
        }

        public String taskId() {
            return taskId;
        }

        public String status() {
            return status;
        }

        public int checkResultCount() {
            return checkResultCount;
        }
    }

    public VcenterEvcClient(
            URI apiRoot,
            String sessionId,
            Duration requestTimeout,
            int maxPolls) {
        throw new UnsupportedOperationException("TODO");
    }

    public ApplyResult applySafely(
            String clusterId,
            EvcMode desiredMode) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }
}
