import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;
import java.util.Objects;

/**
 * Minimal client for the VCF 9.1 SDDC Manager operation selected in
 * docs/contract.json. This exercise intentionally uses only the Java standard
 * library.
 */
public final class VcfDomainInventoryClient {
    private final URI baseUri;
    private final String accessToken;
    private final int pageSize;
    private final Duration requestTimeout;
    private final HttpClient http;

    public record Domain(String id, String name, String status, String type) {
    }

    public VcfDomainInventoryClient(
            URI baseUri,
            String accessToken,
            int pageSize,
            Duration requestTimeout) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.accessToken = Objects.requireNonNull(accessToken, "accessToken");
        this.pageSize = pageSize;
        this.requestTimeout = Objects.requireNonNull(requestTimeout, "requestTimeout");
        this.http = HttpClient.newBuilder()
                .connectTimeout(requestTimeout)
                .build();
    }

    /**
     * Retrieves every getDomains page and returns one deterministic inventory.
     */
    public List<Domain> listAllDomains() throws IOException, InterruptedException {
        // TODO: implement the spec-derived paginated collection workflow.
        throw new UnsupportedOperationException("Not implemented");
    }
}
