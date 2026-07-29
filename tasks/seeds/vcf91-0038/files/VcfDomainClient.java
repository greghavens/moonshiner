import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;
import java.util.Objects;

/**
 * Minimal client for the VCF 9.1 SDDC Manager operations selected in
 * docs/contract.json. This exercise intentionally uses only the Java standard
 * library.
 */
public final class VcfDomainClient {
    private final URI baseUri;
    private final Duration requestTimeout;
    private final HttpClient http;

    public record Domain(String id, String name, String status, String type) {
    }

    public VcfDomainClient(URI baseUri, Duration requestTimeout) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.requestTimeout = Objects.requireNonNull(requestTimeout, "requestTimeout");
        this.http = HttpClient.newBuilder()
                .connectTimeout(requestTimeout)
                .build();
    }

    /**
     * Creates a token pair and retrieves each requested domain. If the access
     * token expires part way through the batch, refresh it and resume at the
     * interrupted domain without losing completed results.
     */
    public List<Domain> collectDomains(
            String username,
            String password,
            String apiKey,
            String idToken,
            List<String> domainIds) throws IOException, InterruptedException {
        // TODO: implement the spec-derived token, domain, and refresh workflow.
        throw new UnsupportedOperationException("Not implemented");
    }
}
