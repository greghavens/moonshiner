import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.util.Objects;

/**
 * Minimal Java 17 client surface for the VCF Installer integration.
 *
 * <p>The implementation intentionally belongs in this single source file and
 * may use only the Java standard library.</p>
 */
public final class VcfInstallerClient {
    private final URI baseUri;
    private final String accessToken;
    private final HttpClient httpClient;

    public VcfInstallerClient(URI baseUri, String accessToken, HttpClient httpClient) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.accessToken = Objects.requireNonNull(accessToken, "accessToken");
        this.httpClient = Objects.requireNonNull(httpClient, "httpClient");
    }

    /** Desired online depot credentials. A null or blank activation code is unset. */
    public record DepotUpdate(String downloadToken, String downloadActivationCode) {
    }

    /** Effective depot credentials decoded from the accepted response. */
    public record DepotSettings(String downloadToken, String downloadActivationCode) {
    }

    /** Replace the online depot settings, retrying only safe ambiguous failures. */
    public DepotSettings updateDepotSettings(DepotUpdate update, int maxRetries)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException(
                "TODO: implement idempotently retryable updateDepotSettings");
    }
}
