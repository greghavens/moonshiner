import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.Objects;

/**
 * Minimal Java 17 client for the VCF 9.1 SDDC Manager operations projected in
 * docs/contract.json.
 */
public final class VcfSessionClient {
    private final URI baseUri;
    private final String accessToken;
    private final String refreshTokenId;
    private final Duration requestTimeout;
    private final HttpClient http;

    /**
     * Query parameters from the getCredentials operation, in specification
     * order. A null component means that the optional parameter is unset.
     */
    public record CredentialQuery(
            String resourceName,
            String resourceIp,
            String resourceType,
            String domainName,
            String pageNumber,
            String pageSize,
            String accountType) {
    }

    public VcfSessionClient(
            URI baseUri,
            String accessToken,
            String refreshTokenId,
            Duration requestTimeout) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.accessToken = Objects.requireNonNull(accessToken, "accessToken");
        this.refreshTokenId = Objects.requireNonNull(refreshTokenId, "refreshTokenId");
        this.requestTimeout = Objects.requireNonNull(requestTimeout, "requestTimeout");
        this.http = HttpClient.newBuilder()
                .connectTimeout(requestTimeout)
                .build();
    }

    /**
     * Invokes getCredentials. If an in-flight request is rejected with 401
     * after a concurrent refresh, it may be replayed once with the new bearer.
     */
    public String getCredentials(CredentialQuery query)
            throws IOException, InterruptedException {
        // TODO: implement the contract-pinned GET and cutover-safe replay.
        throw new UnsupportedOperationException("Not implemented");
    }

    /**
     * Invokes refreshAccessToken and atomically publishes its returned bearer.
     *
     * @return the newly published access token
     */
    public String refreshAccessToken() throws IOException, InterruptedException {
        // TODO: implement the contract-pinned refresh and atomic publication.
        throw new UnsupportedOperationException("Not implemented");
    }
}
