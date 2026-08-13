import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;
import java.util.Objects;

/** Minimal client for the focused contract in docs/contract.json. */
public final class VcfOperationsClient {
    public record SymptomDefinition(
            String id,
            String name,
            String adapterKindKey,
            String resourceKindKey) {
    }

    private final URI baseUri;
    private final String authorization;
    private final int pageSize;
    private final Duration requestTimeout;
    private final HttpClient httpClient;

    public VcfOperationsClient(
            URI baseUri,
            String authorization,
            int pageSize,
            Duration requestTimeout) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.authorization = Objects.requireNonNull(authorization, "authorization");
        this.requestTimeout = Objects.requireNonNull(requestTimeout, "requestTimeout");
        if (pageSize <= 0) {
            throw new IllegalArgumentException("pageSize must be positive");
        }
        this.pageSize = pageSize;
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(requestTimeout)
                .build();
    }

    /** Retrieves and globally orders every symptom definition. */
    public List<SymptomDefinition> listAllSymptomDefinitions()
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }
}
