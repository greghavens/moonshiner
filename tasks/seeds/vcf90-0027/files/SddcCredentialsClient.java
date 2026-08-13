import java.io.IOException;
import java.net.http.HttpClient;
import java.util.List;
import java.util.Objects;

/**
 * Single-file client for the two VMware Cloud Foundation 9.0 SDDC Manager
 * operations named by the pinned contract in {@code docs/contract.json}:
 * {@code createToken} (POST /v1/tokens) and {@code getCredentials}
 * (GET /v1/credentials).
 *
 * <p>The contract was projected from
 * {@code specifications/sddc-manager/sddc-manager-openapi.json} in the
 * Apache-2.0 {@code vmware/vcf-api-specs} repository at tag {@code 9.0.0.0}
 * (commit {@code 85151f6b1bb58f13b6ac0304bfec53904bea085f}). Provenance lives
 * in {@code docs/official_sources.json}.
 *
 * <p>TODO: implement the three public methods below. The declarations, the
 * {@link Credential} projection and {@link ApiException} are the acceptance
 * surface that {@code TestMain} compiles against - keep them as they are.
 */
public final class SddcCredentialsClient {

    /** The subset of {@code Credential} / {@code AuthenticatedResource} this client projects. */
    public record Credential(
            String id,
            String credentialType,
            String accountType,
            String username,
            String resourceId,
            String resourceName,
            String resourceType,
            List<String> domainNames) {
    }

    /** A non-2xx response, carrying the status and the {@code Error} body fields. */
    public static final class ApiException extends IOException {

        private static final long serialVersionUID = 1L;

        private final int statusCode;
        private final String errorCode;

        public ApiException(int statusCode, String errorCode, String message) {
            super(message);
            this.statusCode = statusCode;
            this.errorCode = errorCode;
        }

        public int statusCode() {
            return statusCode;
        }

        public String errorCode() {
            return errorCode;
        }
    }

    private final String baseUrl;
    private final HttpClient httpClient;

    private String accessToken;

    /** Records the target and transport. Performs no I/O. */
    public SddcCredentialsClient(String baseUrl, HttpClient httpClient) {
        Objects.requireNonNull(baseUrl, "baseUrl");
        this.httpClient = Objects.requireNonNull(httpClient, "httpClient");
        String trimmed = baseUrl.strip();
        if (trimmed.isEmpty()) {
            throw new IllegalArgumentException("baseUrl must not be blank");
        }
        this.baseUrl = trimmed.endsWith("/") ? trimmed.substring(0, trimmed.length() - 1) : trimmed;
    }

    /** createToken with a username/password TokenCreationSpec. */
    public void authenticateWithPassword(String username, String password)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("authenticateWithPassword is not implemented yet");
    }

    /** createToken with an api-key-only TokenCreationSpec. */
    public void authenticateWithApiKey(String apiKey) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("authenticateWithApiKey is not implemented yet");
    }

    /**
     * Reads every page of getCredentials and returns the complete collection in
     * the required stable order.
     *
     * @param resourceType optional filter; {@code null} means the parameter is not sent at all
     * @param pageSize     page size to request, 1 or greater
     */
    public List<Credential> listCredentials(String resourceType, int pageSize)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("listCredentials is not implemented yet");
    }
}
