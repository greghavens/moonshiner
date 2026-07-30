import java.io.IOException;
import java.net.URI;
import java.time.Duration;
import java.util.List;

/**
 * Complete this dependency-free VCF 9.1 vCenter Automation client.
 */
public final class VcenterLibraryClient {
    public static final String OPERATION_ID =
            "Content.LocalLibrary_create";

    public enum BackingType {
        DATASTORE,
        OTHER
    }

    /**
     * Null distinguishes the inapplicable backing member from a supplied one.
     */
    public record StorageBacking(
            BackingType type,
            String datastoreId,
            String storageUri) {
    }

    /**
     * A null description is unset; an empty description is explicitly set.
     */
    public record LibrarySpec(
            String name,
            List<StorageBacking> storageBackings,
            String description) {
    }

    public record CreateResult(
            String operationId,
            String libraryId,
            String clientToken,
            int attempts) {
    }

    public static final class VcenterApiException extends IOException {
        private final String operationId;
        private final int statusCode;
        private final int attempts;
        private final byte[] responseBody;

        public VcenterApiException(
                String operationId,
                int statusCode,
                int attempts,
                byte[] responseBody) {
            super("vCenter API request failed with HTTP " + statusCode);
            this.operationId = operationId;
            this.statusCode = statusCode;
            this.attempts = attempts;
            this.responseBody = responseBody.clone();
        }

        public String operationId() {
            return operationId;
        }

        public int statusCode() {
            return statusCode;
        }

        public int attempts() {
            return attempts;
        }

        public byte[] responseBody() {
            return responseBody.clone();
        }
    }

    public static final class ProtocolException extends IOException {
        private final String operationId;
        private final int attempts;

        public ProtocolException(String operationId, int attempts) {
            super("vCenter response violated the contract");
            this.operationId = operationId;
            this.attempts = attempts;
        }

        public String operationId() {
            return operationId;
        }

        public int attempts() {
            return attempts;
        }
    }

    public static final class RetryExhaustedException extends IOException {
        private final String operationId;
        private final int attempts;

        public RetryExhaustedException(String operationId, int attempts) {
            super("vCenter transport failed after the retry limit");
            this.operationId = operationId;
            this.attempts = attempts;
        }

        public String operationId() {
            return operationId;
        }

        public int attempts() {
            return attempts;
        }
    }

    public VcenterLibraryClient(
            URI baseUri,
            String sessionId,
            Duration requestTimeout) {
        throw new UnsupportedOperationException("TODO");
    }

    public CreateResult createLocalLibrary(
            String clientToken,
            LibrarySpec spec) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }
}
