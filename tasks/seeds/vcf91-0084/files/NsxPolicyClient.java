import java.io.IOException;
import java.net.http.HttpClient;

/**
 * Complete this dependency-free VCF 9.1 NSX Policy client.
 */
public final class NsxPolicyClient {
    public record Tier1DescriptionPatch(String description) {}

    public record UpdateResult(
            String tier1Id,
            String precheckState,
            boolean changed
    ) {}

    public static final class PrecheckFailed extends RuntimeException {
        private static final long serialVersionUID = 1L;

        private final String tier1Id;
        private final String state;
        private final Long failureCode;
        private final String failureMessage;

        PrecheckFailed(
                String tier1Id,
                String state,
                Long failureCode,
                String failureMessage
        ) {
            super("Tier-1 gateway-state precheck did not pass");
            this.tier1Id = tier1Id;
            this.state = state;
            this.failureCode = failureCode;
            this.failureMessage = failureMessage;
        }

        public String tier1Id() {
            return tier1Id;
        }

        public String state() {
            return state;
        }

        public Long failureCode() {
            return failureCode;
        }

        public String failureMessage() {
            return failureMessage;
        }
    }

    public static final class ProtocolException extends RuntimeException {
        private static final long serialVersionUID = 1L;

        ProtocolException(String message) {
            super(message);
        }
    }

    public static final class NsxPolicyException extends RuntimeException {
        private static final long serialVersionUID = 1L;

        private final int statusCode;
        private final String responseBody;

        NsxPolicyException(int statusCode, String responseBody) {
            super("NSX Policy request failed with HTTP " + statusCode);
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
            HttpClient httpClient
    ) {
        // TODO
    }

    public UpdateResult updateTier1DescriptionIfReady(
            String tier1Id,
            Tier1DescriptionPatch patch,
            String enforcementPointPath,
            String source
    ) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }
}
