import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.util.Map;

/** Dependency-free client for the pinned VCF 9.1 Log Management contract. */
public final class VcfLogForwarderClient {
    public VcfLogForwarderClient(URI baseUri, String jwtToken) {
        // TODO: initialize the HTTP client and immutable request configuration.
    }

    public UpdateResult updateLogForwarder(String id, LogForwarder update)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO: implement updateLogForwarder");
    }

    /**
     * Every component corresponds to a LogForwarder request property except the
     * response-only id. A null component is unset and must not appear in JSON.
     */
    public record LogForwarder(
            String certificate,
            Integer connectionRefreshInterval,
            Map<String, Object> constraints,
            Boolean enabled,
            Boolean forwardComplementaryFields,
            String host,
            String name,
            Integer port,
            String protocol,
            Boolean sslEnabled,
            Map<String, String> tags,
            String transportProtocol,
            Integer workerCount) {
    }

    public record UpdateResult(int statusCode, String body) {
    }

    public static final class VcfApiException extends IOException {
        private final int statusCode;
        private final String responseBody;

        public VcfApiException(int statusCode, String responseBody) {
            super("VCF Log Management returned HTTP " + statusCode);
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
}
