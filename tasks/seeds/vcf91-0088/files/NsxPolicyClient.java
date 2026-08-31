import java.io.IOException;
import java.time.Duration;
import java.util.List;

/**
 * Complete this dependency-free VCF 9.1 NSX Policy client.
 */
public final class NsxPolicyClient {
    public record Segment(String id, String displayName) {
    }

    public static final class NsxPolicyException extends IOException {
        private final int statusCode;
        private final String responseBody;

        public NsxPolicyException(int statusCode, String responseBody) {
            super("ListAllInfraSegments failed with HTTP " + statusCode);
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
            String username,
            String password,
            Duration requestTimeout) {
        throw new UnsupportedOperationException("TODO");
    }

    public List<Segment> listAllSegments()
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }
}
