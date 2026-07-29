import java.io.IOException;
import java.time.Duration;

/**
 * Complete this dependency-free VCF 9.1 NSX Policy client.
 */
public final class NsxPolicyClient {
    public record Credentials(String username, String password) {
    }

    public record RotationResult(
            long oldGeneration,
            long newGeneration,
            boolean retired) {
    }

    public static final class RotationTimeoutException extends IOException {
        private final long oldGeneration;
        private final long newGeneration;
        private final int pendingRequests;

        public RotationTimeoutException(
                long oldGeneration,
                long newGeneration,
                int pendingRequests) {
            super("credential rotation timed out while requests remained in flight");
            this.oldGeneration = oldGeneration;
            this.newGeneration = newGeneration;
            this.pendingRequests = pendingRequests;
        }

        public long oldGeneration() {
            return oldGeneration;
        }

        public long newGeneration() {
            return newGeneration;
        }

        public int pendingRequests() {
            return pendingRequests;
        }
    }

    public static final class NsxPolicyException extends IOException {
        private final int statusCode;
        private final String responseBody;

        public NsxPolicyException(int statusCode, String responseBody) {
            super("ListTier1 failed with HTTP " + statusCode);
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

    public NsxPolicyClient(
            String managerBaseUrl,
            Credentials initialCredentials,
            Duration requestTimeout) {
        throw new UnsupportedOperationException("TODO");
    }

    public long credentialGeneration() {
        throw new UnsupportedOperationException("TODO");
    }

    public String listTier1s(String cursor, Long pageSize)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }

    public RotationResult rotateCredentials(
            Credentials replacement,
            Duration drainTimeout,
            Runnable retireOld)
            throws InterruptedException, RotationTimeoutException {
        throw new UnsupportedOperationException("TODO");
    }
}
